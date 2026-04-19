"""Displaywall Sync — Hardware-Counter-basierte Taktsynchronisation.

Cross-Pi-Sync ueber UDP-Broadcast mit CLOCK_MONOTONIC_RAW als Zeitbasis.
Unabhaengig von NTP, Systemuhr und Netzwerk-Jitter.

Lokaler Sync (mehrere Displays auf einem Pi) nutzt threading.Barrier.

Protokoll v2 ("dw2") — Legacy:
  Master sendet: { v: "dw2", m_now: <s>, m_next: <s> }

Protokoll v3 ("dw3") — Tick-Counter:
  Master sendet: { v: "dw3", t0: <s>, m_now: <s>, tick: <int> }
  t0    = gemeinsamer Startzeitpunkt (CLOCK_MONOTONIC_RAW, Sekunden)
  m_now = aktueller HW-Counter (fuer PLL-Offset)
  tick  = aktuelle Tick-Nummer (zur Verifikation)
  Slave berechnet: local_tick = floor(hw_now() + avg_offset - t0)
"""

import collections
import json
import logging
import socket
import threading
import time

SYNC_PORT = 1666
SYNC_MAGIC = "dw2"  # Legacy Protokoll-Version
SYNC_MAGIC_V3 = "dw3"  # Tick-Counter Protokoll

# Hardware-Counter: Nanosekunden seit Boot, unabhaengig von NTP
_CLOCK = time.CLOCK_MONOTONIC_RAW


def hw_now_ns():
    """Aktueller Hardware-Counter in Nanosekunden."""
    return time.clock_gettime_ns(_CLOCK)


def hw_now():
    """Aktueller Hardware-Counter in Sekunden (float)."""
    return time.clock_gettime(_CLOCK)


class TickClock:
    """Globaler 1-Sekunden-Takt aus CLOCK_MONOTONIC_RAW.

    Tick-Nummer = floor(hw_now() - T0).
    Deterministisch, zustandslos, kein Timer.
    """

    def __init__(self, t0=None):
        self.t0 = t0 if t0 is not None else hw_now()

    def tick(self):
        """Aktuelle Tick-Nummer (ganzzahlig, ab 0)."""
        return int(hw_now() - self.t0)

    def next_tick_hw(self):
        """HW-Counter-Zeitpunkt des naechsten Ticks."""
        return self.t0 + self.tick() + 1


class DisplayCounter:
    """Per-Display Countdown-Zaehler fuer Bildwechsel.

    Jedes Display hat einen Counter, initialisiert mit der Asset-Duration.
    Pro Tick: Counter--, bei 0 → naechstes Bild.
    """

    def __init__(self, playlist, start_index=0):
        self.playlist = playlist  # Liste von Assets mit "duration"
        self.index = start_index
        self._counter = self._get_duration(start_index)
        self._last_tick = None

    def _get_duration(self, index):
        """Duration fuer ein Asset (Sekunden, mind. 1)."""
        if not self.playlist:
            return 5
        item = self.playlist[index % max(len(self.playlist), 1)]
        return max(int(float(item.get("duration", 10))), 1)

    def update(self, current_tick):
        """Pro Tick aufrufen. Returns (should_switch, index) oder (False, None)."""
        if self._last_tick is None:
            self._last_tick = current_tick
            return True, self.index  # Initialer Load

        elapsed = current_tick - self._last_tick
        self._last_tick = current_tick

        if elapsed <= 0:
            return False, None

        self._counter -= elapsed
        if self._counter <= 0:
            self.index = (self.index + 1) % max(len(self.playlist), 1)
            self._counter = self._get_duration(self.index)
            return True, self.index

        return False, None

    def force_next(self):
        """Einmalig zum naechsten Bild springen."""
        self.index = (self.index + 1) % max(len(self.playlist), 1)
        self._counter = self._get_duration(self.index)
        return self.index

    def force_prev(self):
        """Einmalig zum vorherigen Bild springen."""
        self.index = (self.index - 1) % max(len(self.playlist), 1)
        self._counter = self._get_duration(self.index)
        return self.index

    def set_playlist(self, playlist):
        """Playlist austauschen, Index zuruecksetzen."""
        self.playlist = playlist
        self.index = 0
        self._counter = self._get_duration(0)
        self._last_tick = None

    def set_random_index(self):
        """Zufaelligen Index setzen (Shuffle)."""
        import random
        if self.playlist:
            self.index = random.randint(0, len(self.playlist) - 1)
        return self.index

    @property
    def remaining(self):
        """Verbleibende Ticks bis zum naechsten Wechsel."""
        return self._counter


class DeterministicPlaylist:
    """Deterministische Positionsberechnung aus globalem Tick (MIDI-Prinzip).

    Statt einen Counter zu dekrementieren wird die Position rein aus dem
    aktuellen Tick berechnet:  position = tick_in_cycle -> Index.
    Kein akkumulierender Fehler, kein Drift, kein Re-Sync noetig.

    Unterstuetzt unterschiedliche Durations pro Asset.
    """

    def __init__(self, playlist):
        self.playlist = playlist
        self._build_schedule()
        self._last_index = None
        self._offset = 0  # Tick-Offset fuer force_next/prev

    def _build_schedule(self):
        """Berechnet kumulierte Wechselzeitpunkte und Gesamt-Zyklusdauer."""
        self._boundaries = []  # Kumulierte Ticks am Ende jedes Assets
        cumulative = 0
        for item in self.playlist:
            dur = max(int(float(item.get("duration", 10))), 1)
            cumulative += dur
            self._boundaries.append(cumulative)
        self._cycle_len = cumulative if cumulative > 0 else 1

    def update(self, current_tick):
        """Aus dem globalen Tick den aktuellen Index ableiten.

        Returns (should_switch, new_index) oder (False, None).
        """
        if not self.playlist:
            return False, None

        adjusted = current_tick + self._offset
        pos = adjusted % self._cycle_len
        if pos < 0:
            pos += self._cycle_len

        # Binaersuche im Schedule: erstes Asset dessen Grenze > pos
        new_index = 0
        for i, boundary in enumerate(self._boundaries):
            if pos < boundary:
                new_index = i
                break

        if new_index != self._last_index:
            self._last_index = new_index
            return True, new_index

        return False, None

    def force_next(self):
        """Einmalig vorspringen — verschiebt den Offset."""
        if not self.playlist:
            return 0
        # Offset um Duration des aktuellen Assets erhoehen
        idx = self._last_index if self._last_index is not None else 0
        dur = max(int(float(self.playlist[idx].get("duration", 10))), 1)
        self._offset += dur
        new_idx = (idx + 1) % len(self.playlist)
        self._last_index = new_idx
        return new_idx

    def force_prev(self):
        """Einmalig zurueckspringen — verschiebt den Offset."""
        if not self.playlist:
            return 0
        idx = self._last_index if self._last_index is not None else 0
        prev_idx = (idx - 1) % len(self.playlist)
        dur = max(int(float(self.playlist[prev_idx].get("duration", 10))), 1)
        self._offset -= dur
        self._last_index = prev_idx
        return prev_idx

    def set_random_index(self):
        """Zufaelligen Index setzen — Offset so anpassen dass er passt."""
        import random
        if self.playlist:
            target = random.randint(0, len(self.playlist) - 1)
            # Offset berechnen damit update() diesen Index liefert
            target_start = self._boundaries[target - 1] if target > 0 else 0
            self._offset = target_start  # Naechster update() trifft diesen Index
            self._last_index = target
            return target
        return 0

    def next_switch_tick(self, current_tick):
        """Tick-Nummer des naechsten Bildwechsels vorhersagen."""
        if not self.playlist:
            return current_tick + 1
        pos = (current_tick + self._offset) % self._cycle_len
        if pos < 0:
            pos += self._cycle_len
        for boundary in self._boundaries:
            if pos < boundary:
                return current_tick + (boundary - pos)
        return current_tick + (self._cycle_len - pos + self._boundaries[0])

    def peek_next_index(self, current_tick):
        """Index des naechsten Bildes vorhersagen (ohne State zu aendern)."""
        if not self.playlist:
            return 0
        switch_tick = self.next_switch_tick(current_tick)
        pos = (switch_tick + self._offset) % self._cycle_len
        if pos < 0:
            pos += self._cycle_len
        for i, boundary in enumerate(self._boundaries):
            if pos < boundary:
                return i
        return 0

    @property
    def index(self):
        return self._last_index if self._last_index is not None else 0

    @property
    def remaining(self):
        """Verbleibende Ticks bis zum naechsten Wechsel (fuer Status-Anzeige)."""
        if not self.playlist or self._last_index is None:
            return 0
        boundary = self._boundaries[self._last_index]
        return boundary - (0 % self._cycle_len)  # Approximation


class SyncMaster:
    """Sendet bei jedem Bildwechsel den naechsten Wechselzeitpunkt per UDP-Broadcast.

    Der Master rechnet intern mit CLOCK_MONOTONIC_RAW. Das Paket enthaelt:
    - m_now:  Hardware-Counter JETZT (Sendezeitpunkt)
    - m_next: Hardware-Counter des naechsten geplanten Bildwechsels
    Der Slave braucht nur die DIFFERENZ (m_next - m_now), nicht den absoluten Wert.
    """

    def __init__(self, slave_ips=None, broadcast_ip="255.255.255.255", port=SYNC_PORT):
        self.port = port
        self.broadcast_ip = broadcast_ip
        self.slave_ips = slave_ips or []  # Direkte Unicast-Adressen
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def send_tick(self, next_switch_hw, tick_clock=None):
        """Sendet Sync-Paket an Slaves.

        Sendet v3 (Tick-Counter) wenn tick_clock gegeben, sonst v2 (Legacy).
        """
        now = hw_now()
        if tick_clock:
            msg = json.dumps({
                "v": SYNC_MAGIC_V3,
                "t0": tick_clock.t0,
                "m_now": now,
                "tick": tick_clock.tick(),
            }).encode()
        else:
            msg = json.dumps({
                "v": SYNC_MAGIC,
                "m_now": now,
                "m_next": next_switch_hw,
            }).encode()
        try:
            # Broadcast (fuer unbekannte Slaves)
            self.sock.sendto(msg, (self.broadcast_ip, self.port))
            # Unicast an bekannte Slaves (WLAN blockiert oft Broadcast)
            for ip in self.slave_ips:
                self.sock.sendto(msg, (ip, self.port))
        except Exception as e:
            logging.warning("SyncMaster: Sende-Fehler: %s", e)

    def close(self):
        self.sock.close()


class SyncSlave:
    """Empfaengt Sync-Ticks vom Master und berechnet lokale Wechselzeitpunkte.

    Software-PLL: Misst den Offset zwischen Master- und Slave-Counter
    ueber mehrere Pakete und glaettet per gleitendem Durchschnitt.

    Nutzung:
        slave = SyncSlave()
        slave.start()

        # Im Playback-Loop:
        local_target = slave.get_next_switch_local()
        if local_target > 0:
            busy_wait_until_hw(local_target)
            # Bild wechseln
    """

    PLL_WINDOW = 8       # Anzahl Samples fuer gleitenden Durchschnitt
    CONVERGE_MIN = 3     # Mindest-Samples bevor PLL als konvergiert gilt

    def __init__(self, port=SYNC_PORT, timeout=30):
        self.port = port
        self.timeout = timeout  # Sekunden ohne Signal → autonom
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

        # PLL-State
        self._offsets = collections.deque(maxlen=self.PLL_WINDOW)
        self._last_rx_hw = 0.0       # Lokaler HW-Counter bei letztem Empfang
        self._last_delta = 0.0       # m_next - m_now aus letztem Paket
        self._converged = False

        # v3-State (Tick-Counter)
        self._master_t0 = None       # Gemeinsamer Startzeitpunkt
        self._master_tick = 0        # Letzte empfangene Tick-Nummer
        self._v3_active = False      # True sobald v3-Paket empfangen

    def start(self):
        """Listener-Thread starten."""
        self._running = True
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _listen(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(5)
        sock.bind(("", self.port))
        logging.info("SyncSlave: lausche auf Port %d (Hardware-Counter PLL)", self.port)

        while self._running:
            try:
                data, addr = sock.recvfrom(512)
                rx_hw = hw_now()  # Sofort lokalen Counter messen
                msg = json.loads(data.decode())
                version = msg.get("v")
                if version not in (SYNC_MAGIC, SYNC_MAGIC_V3):
                    continue

                m_now = msg["m_now"]    # Master-Counter bei Senden

                # Offset = Differenz zwischen Master- und Slave-Counter
                offset = m_now - rx_hw

                with self._lock:
                    self._offsets.append(offset)
                    self._last_rx_hw = rx_hw
                    self._converged = len(self._offsets) >= self.CONVERGE_MIN

                    if version == SYNC_MAGIC_V3:
                        # v3: Tick-Counter Protokoll
                        new_t0 = msg["t0"]
                        # PLL-Reset bei T0-Wechsel (Master-Neustart)
                        if self._master_t0 is not None and new_t0 != self._master_t0:
                            self._offsets.clear()
                            self._converged = False
                            logging.info("SyncSlave: PLL-Reset (T0 geaendert: %.3f -> %.3f)",
                                         self._master_t0, new_t0)
                            self._offsets.append(offset)  # Erstes Sample neu
                        self._master_t0 = new_t0
                        self._master_tick = msg["tick"]
                        self._v3_active = True
                        self._last_delta = 0  # v3 nutzt kein m_next
                    else:
                        # v2: Legacy
                        m_next = msg["m_next"]
                        self._last_delta = m_next - m_now

                    if len(self._offsets) == self.CONVERGE_MIN:
                        logging.info("SyncSlave: PLL konvergiert (%d Samples, %s)",
                                     self.CONVERGE_MIN, version)

            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logging.warning("SyncSlave: Empfangs-Fehler: %s", e)

        sock.close()

    def _avg_offset(self):
        """Geglätteter Offset (gleitender Durchschnitt)."""
        if not self._offsets:
            return 0.0
        return sum(self._offsets) / len(self._offsets)

    def get_next_switch_local(self):
        """Naechsten Wechselzeitpunkt in lokaler CLOCK_MONOTONIC_RAW (Sekunden).

        Gibt 0.0 zurueck wenn:
        - Kein Master-Signal empfangen
        - PLL noch nicht konvergiert
        - Signal aelter als timeout
        """
        with self._lock:
            if not self._converged:
                return 0.0
            # Timeout pruefen (in HW-Zeit)
            if hw_now() - self._last_rx_hw > self.timeout:
                return 0.0

            # Master sagt: "naechster Wechsel ist m_next"
            # Lokal: m_next - offset = lokaler Zeitpunkt
            # offset = master - local → local = master - offset
            avg_off = self._avg_offset()
            # m_next des Masters in lokale Zeit umrechnen:
            # master_next = last_rx_hw + last_delta + (Netzwerk-Latenz ≈ 0)
            # Aber wir haben den Offset: local = master - offset
            # Also: local_next = (m_now + last_delta) - offset
            #                   = (m_now - offset) + last_delta
            #                   = last_rx_hw + last_delta
            #   (weil m_now - offset ≈ rx_hw per Definition)
            # Vereinfacht: Der Wechsel ist last_delta nach dem letzten Empfang
            return self._last_rx_hw + self._last_delta

    def has_master(self):
        """True wenn PLL konvergiert und Signal aktuell."""
        with self._lock:
            if not self._converged:
                return False
            return hw_now() - self._last_rx_hw < self.timeout

    def is_v3(self):
        """True wenn Master im v3 Tick-Counter Modus."""
        with self._lock:
            return self._v3_active and self._converged

    def get_local_tick(self):
        """Lokale Tick-Nummer berechnen (v3 Modus).

        Gibt (tick, t0) zurueck, oder (None, None) wenn v3 nicht aktiv.
        """
        with self._lock:
            if not self._v3_active or not self._converged:
                return None, None
            if hw_now() - self._last_rx_hw > self.timeout:
                return None, None
            avg_off = self._avg_offset()
            # Lokale Zeit in Master-Zeit umrechnen: master_time = local_time + offset
            # tick = floor(master_time - t0) = floor(local_time + offset - t0)
            local_tick = int(hw_now() + avg_off - self._master_t0)
            return local_tick, self._master_t0

    def get_master_t0(self):
        """Master-T0 zurueckgeben (fuer lokale TickClock)."""
        with self._lock:
            return self._master_t0

    def get_offset_ms(self):
        """Aktueller Offset in Millisekunden (fuer Diagnose)."""
        with self._lock:
            return self._avg_offset() * 1000


def busy_wait_until_hw(target_hw):
    """Praezises Warten bis zum Zielzeitpunkt (CLOCK_MONOTONIC_RAW, Sekunden).

    Grob schlafen bis 20ms vor Ziel, dann Busy-Wait fuer Praezision.
    """
    remaining = target_hw - hw_now()
    if remaining > 0.02:
        time.sleep(remaining - 0.02)
    # Busy-Wait die letzten ~20ms
    while hw_now() < target_hw:
        pass


# --- Abwaertskompatibilitaet (fuer bestehende Aufrufe) ---

def busy_wait_until(target_epoch):
    """Praezises Warten bis zum Zielzeitpunkt (wall-clock, Sekunden).

    Fuer lokalen Sync auf dem Head-Pi (gleiche Maschine, kein Cross-Pi).
    """
    remaining = target_epoch - time.time()
    if remaining > 0.02:
        time.sleep(remaining - 0.02)
    while time.time() < target_epoch:
        pass
