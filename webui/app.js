/* Displaywall VJ-Manager — App-Logik */

var assets = [];
var wallConfig = null;
var selectedMonitor = null;

/* ============================================
   TAB-NAVIGATION
   ============================================ */

document.querySelectorAll('.tab-btn').forEach(function (btn) {
  btn.addEventListener('click', function () {
    document.querySelectorAll('.tab-btn').forEach(function (b) { b.classList.remove('active'); });
    btn.classList.add('active');

    var tabId = btn.dataset.tab;
    var color = document.getElementById(tabId).dataset.color;

    document.querySelectorAll('.tab-content').forEach(function (tc) { tc.classList.remove('active'); });
    document.getElementById(tabId).classList.add('active');

    var h1 = document.querySelector('#' + tabId + ' h1');
    if (h1) h1.style.color = color;

    // Canvas neu zeichnen wenn Tab sichtbar wird
    if (tabId === 'tabCanvas') {
      setTimeout(function () { resizeCanvas(); renderCanvas(); }, 50);
    }
  });
});

// Initiale Farbe
(function () {
  var first = document.querySelector('.tab-content.active');
  if (first) {
    var h1 = first.querySelector('h1');
    if (h1) h1.style.color = first.dataset.color;
  }
})();

/* ============================================
   OUTPUT
   ============================================ */

function showOutput(msg) {
  document.getElementById('outputText').textContent = msg;
}

/* ============================================
   DATEN LADEN
   ============================================ */

async function loadAssets() {
  try {
    var res = await fetch('/api/assets');
    assets = await res.json();
    renderPool();
    renderPoolTab();
  } catch (e) {
    showOutput('Fehler: ' + e.message);
  }
}

async function loadWallConfig() {
  try {
    var res = await fetch('/api/wall');
    wallConfig = await res.json();
    // Canvas init braucht wallConfig
    setTimeout(function () {
      resizeCanvas();
      renderCanvas();
    }, 100);
    renderMonitorSelector();
    renderDisplaySettings();
    renderPoolTab();
    if (selectedMonitor) renderPlaylist(selectedMonitor);
    startAllTrackers();
  } catch (e) {
    showOutput('Fehler: ' + e.message);
  }
}

async function loadStatus() {
  try {
    var res = await fetch('/api/status');
    var status = await res.json();
    var slavesRes = await fetch('/api/slaves');
    var slaves = await slavesRes.json();
    renderStatus(status, slaves);
    updateToolbarStatus(status);
  } catch (e) { /* still */ }
}

async function saveWallConfig() {
  try {
    await fetch('/api/wall', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(wallConfig),
    });
    showOutput('Gespeichert');
  } catch (e) {
    showOutput('Fehler: ' + e.message);
  }
}

async function savePlaylist(monitorId) {
  try {
    await fetch('/api/playlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        output: monitorId,
        playlist: (wallConfig.playlists || {})[monitorId] || [],
      }),
    });
    showOutput('Playlist gespeichert: ' + monitorId);
    flashSaveIndicator();
  } catch (e) {
    showOutput('Fehler: ' + e.message);
  }
}

function toggleShuffle() {
  if (!selectedMonitor || !wallConfig) {
    showOutput('Erst einen Monitor auswaehlen');
    return;
  }
  var pl = (wallConfig.playlists || {})[selectedMonitor];
  if (!pl) return;

  // shuffle Flag pro Playlist toggeln
  // Gespeichert als Property auf der Playlist-Ebene in wallConfig
  if (!wallConfig.playback) wallConfig.playback = {};
  var current = (wallConfig.playback[selectedMonitor] || {}).shuffle || false;
  if (!wallConfig.playback[selectedMonitor]) wallConfig.playback[selectedMonitor] = {};
  wallConfig.playback[selectedMonitor].shuffle = !current;

  saveWallConfig();
  updateShuffleBtn();
  showOutput('Zufall ' + (!current ? 'AN' : 'AUS') + ': ' + selectedMonitor);
}

function updateShuffleBtn() {
  var btn = document.getElementById('shuffleBtn');
  if (!btn || !selectedMonitor || !wallConfig) return;
  var active = wallConfig.playback &&
    wallConfig.playback[selectedMonitor] &&
    wallConfig.playback[selectedMonitor].shuffle;
  btn.classList.toggle('active', !!active);
}

function flashSaveIndicator() {
  var el = document.getElementById('saveIndicator');
  if (!el) return;
  el.textContent = '\u2713 Gespeichert';
  el.classList.add('visible');
  setTimeout(function () { el.classList.remove('visible'); }, 2000);
}

/* ============================================
   POOL (Sidebar — immer sichtbar)
   ============================================ */

function escHtml(s) {
  var d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function badgeClass(mime) {
  if (mime.includes('image')) return 'badge-image';
  if (mime.includes('video')) return 'badge-video';
  return 'badge-web';
}

function badgeLabel(mime) {
  if (mime.includes('image')) return 'Bild';
  if (mime.includes('video')) return 'Video';
  return 'Web';
}

function renderPool() {
  var list = document.getElementById('poolList');
  list.innerHTML = '';

  document.getElementById('poolCount').textContent = assets.length + ' Assets';

  if (!assets.length) {
    list.innerHTML = '<div class="empty-hint">Keine Assets.<br>Upload ueber Anthias UI.</div>';
    return;
  }

  assets.forEach(function (a) {
    var name = a.name.replace(/^2:/, '');
    var div = document.createElement('div');
    div.className = 'pool-item';
    div.draggable = true;

    div.innerHTML =
      '<div class="pool-item-name">' + escHtml(name) + '</div>' +
      '<div class="pool-item-meta">' +
        '<span class="badge ' + badgeClass(a.mimetype) + '">' + badgeLabel(a.mimetype) + '</span>' +
        '<span>' + a.duration + 's</span>' +
      '</div>';

    // Preview-Tooltip bei Mouseover
    if (a.mimetype && a.mimetype.includes('image')) {
      div.addEventListener('mouseenter', function (e) {
        showPreviewTooltip(assetUrl(a.uri), 'image', e);
      });
      div.addEventListener('mousemove', positionTooltip);
      div.addEventListener('mouseleave', hidePreviewTooltip);
    } else if (a.mimetype && a.mimetype.includes('video')) {
      div.addEventListener('mouseenter', function (e) {
        showPreviewTooltip(assetUrl(a.uri), 'video', e);
      });
      div.addEventListener('mousemove', positionTooltip);
      div.addEventListener('mouseleave', hidePreviewTooltip);
    }

    div.addEventListener('dragstart', function (e) {
      hidePreviewTooltip();
      e.dataTransfer.setData('text/plain', JSON.stringify({
        type: 'pool',
        id: a.asset_id,
        name: name,
        duration: a.duration,
        uri: a.uri,
        mimetype: a.mimetype,
      }));
      div.style.opacity = '0.4';
    });

    div.addEventListener('dragend', function () {
      div.style.opacity = '1';
    });

    list.appendChild(div);
  });
}

/* ============================================
   POOL TAB (Asset-Browser mit Farbcodes)
   ============================================ */

function getAssetMonitorColors(assetName) {
  /* Gibt Array von Monitor-Farben zurueck, denen dieses Asset zugewiesen ist. */
  var colors = [];
  if (!wallConfig || !wallConfig.playlists) return colors;
  var playlists = wallConfig.playlists;
  for (var monId in playlists) {
    var pl = playlists[monId];
    for (var i = 0; i < pl.length; i++) {
      var name = (pl[i].asset || pl[i].name || '').replace(/^2:/, '');
      if (name === assetName) {
        colors.push(MONITOR_COLORS[monId] || '#888');
        break;
      }
    }
  }
  return colors;
}

function renderPoolTab() {
  var grid = document.getElementById('poolTabGrid');
  if (!grid) return;
  grid.innerHTML = '';

  var countEl = document.getElementById('poolTabCount');
  if (countEl) countEl.textContent = assets.length + ' Assets';

  if (!assets.length) {
    grid.innerHTML = '<div class="empty-hint">Keine Assets vorhanden. Dateien hochladen.</div>';
    return;
  }

  assets.forEach(function (a) {
    var name = a.name.replace(/^2:/, '');
    var monColors = getAssetMonitorColors(name);
    var borderColor = monColors.length ? monColors[0] : '#555';

    var card = document.createElement('div');
    card.className = 'pool-tab-card';
    card.style.borderColor = borderColor;

    // Thumbnail / Preview
    var thumbHtml = '';
    var url = assetUrl(a.uri);
    if (a.mimetype && a.mimetype.includes('image')) {
      thumbHtml = '<img class="pool-tab-thumb" src="' + url + '" alt="' + escHtml(name) + '" loading="lazy">';
    } else if (a.mimetype && a.mimetype.includes('video')) {
      thumbHtml = '<video class="pool-tab-thumb" src="' + url + '" preload="metadata" muted></video>';
    } else {
      thumbHtml = '<div class="pool-tab-thumb pool-tab-thumb-placeholder">' + badgeLabel(a.mimetype) + '</div>';
    }

    // Farbpunkte fuer zugewiesene Monitore
    var dotsHtml = '';
    if (monColors.length) {
      dotsHtml = '<div class="pool-tab-dots">';
      monColors.forEach(function (c) {
        dotsHtml += '<span class="pool-tab-dot" style="background:' + c + '"></span>';
      });
      dotsHtml += '</div>';
    }

    card.innerHTML =
      thumbHtml +
      '<div class="pool-tab-info">' +
        '<div class="pool-tab-name">' + escHtml(name) + '</div>' +
        '<div class="pool-tab-meta">' +
          '<span class="badge ' + badgeClass(a.mimetype) + '">' + badgeLabel(a.mimetype) + '</span>' +
          '<span>' + a.duration + 's</span>' +
          dotsHtml +
        '</div>' +
      '</div>' +
      (a.mimetype && a.mimetype.includes('image')
        ? '<button class="pool-tab-rotate" title="90\u00b0 drehen" data-id="' + a.asset_id + '">\u21bb</button>'
        : '') +
      '<button class="pool-tab-delete" title="Asset loeschen" data-id="' + a.asset_id + '">&times;</button>';

    // Drag-Support (wie Sidebar-Pool)
    card.draggable = true;
    card.addEventListener('dragstart', function (e) {
      hidePreviewTooltip();
      e.dataTransfer.setData('text/plain', JSON.stringify({
        type: 'pool',
        id: a.asset_id,
        name: name,
        duration: a.duration,
        uri: a.uri,
        mimetype: a.mimetype,
      }));
      card.style.opacity = '0.4';
    });
    card.addEventListener('dragend', function () {
      card.style.opacity = '1';
    });

    // Rotate-Button
    var rotBtn = card.querySelector('.pool-tab-rotate');
    if (rotBtn) {
      rotBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        rotateAsset(a.asset_id, name, e.currentTarget);
      });
    }

    // Delete-Button
    card.querySelector('.pool-tab-delete').addEventListener('click', function (e) {
      e.stopPropagation();
      deleteAsset(a.asset_id, name);
    });

    grid.appendChild(card);
  });
}

async function rotateAsset(assetId, name, btnEl) {
  // Ladeanzeige auf dem Button
  if (btnEl) {
    btnEl.textContent = '\u23f3';
    btnEl.style.pointerEvents = 'none';
  }
  try {
    var res = await fetch('/api/asset/rotate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ asset_id: assetId, filename: name }),
    });
    var data = await res.json();
    if (data.ok) {
      showOutput('Rotiert: ' + name);
      // Nur das eine Thumbnail neu laden (Cache-Bust)
      var bust = '?t=' + Date.now();
      var card = btnEl ? btnEl.closest('.pool-tab-card') : null;
      if (card) {
        var img = card.querySelector('.pool-tab-thumb');
        if (img) img.src = img.src.split('?')[0] + bust;
      }
    } else {
      showOutput('Fehler: ' + (data.error || 'unbekannt'));
    }
  } catch (e) {
    showOutput('Fehler: ' + e.message);
  }
  // Button wiederherstellen
  if (btnEl) {
    btnEl.textContent = '\u21bb';
    btnEl.style.pointerEvents = '';
  }
}

async function deleteAsset(assetId, name) {
  if (!confirm('Asset "' + name + '" wirklich loeschen?')) return;
  try {
    var res = await fetch('/api/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ asset_id: assetId }),
    });
    var data = await res.json();
    if (data.ok) {
      showOutput('Geloescht: ' + name);
      loadAssets();
      loadWallConfig();
    } else {
      showOutput('Fehler: ' + (data.error || 'unbekannt'));
    }
  } catch (e) {
    showOutput('Fehler: ' + e.message);
  }
}

/* ============================================
   MONITOR SELECTOR
   ============================================ */

var MONITOR_COLORS = {
  'head-1': '#e94560', 'head-2': '#ff6b6b',
  'slave1-1': '#4ecdc4', 'slave1-2': '#45b7aa',
  'slave2-1': '#f9ca24', 'slave2-2': '#f0b800',
};

function renderMonitorSelector() {
  var cont = document.getElementById('monitorSelector');
  cont.innerHTML = '';
  if (!wallConfig) return;

  wallConfig.canvas.monitors.forEach(function (mon) {
    var btn = document.createElement('button');
    btn.className = 'mon-btn' + (selectedMonitor === mon.id ? ' active' : '');
    btn.textContent = mon.label || mon.id;
    var col = MONITOR_COLORS[mon.id] || '#555';
    btn.style.borderColor = col;
    if (selectedMonitor === mon.id) {
      btn.style.background = col;
      btn.style.borderColor = col;
    }
    btn.addEventListener('click', function () { selectMonitor(mon.id); });
    cont.appendChild(btn);
  });
}

function selectMonitor(id) {
  selectedMonitor = id;
  renderMonitorSelector();
  renderPlaylist(id);
  renderCanvas();
  // Playback-State sofort holen, dann Preview aktualisieren
  syncPlaybackState().then(function () {
    updateLivePreview(id);
  });

  var mon = findMonitor(id);
  if (mon) {
    document.getElementById('playlistTitle').textContent =
      'Playlist \u2014 ' + (mon.label || mon.id);
  }
  updateShuffleBtn();
}

function findMonitor(id) {
  if (!wallConfig) return null;
  for (var i = 0; i < wallConfig.canvas.monitors.length; i++) {
    if (wallConfig.canvas.monitors[i].id === id) return wallConfig.canvas.monitors[i];
  }
  return null;
}

/* ============================================
   PLAYLIST
   ============================================ */

function renderPlaylist(monitorId) {
  var list = document.getElementById('playlistItems');
  list.innerHTML = '';

  var playlist = (wallConfig.playlists || {})[monitorId] || [];

  if (!playlist.length) {
    list.innerHTML = '<li class="empty-hint">Playlist leer \u2014 Assets aus dem Pool rechts hierher ziehen</li>';
    return;
  }

  playlist.forEach(function (item, idx) {
    var li = document.createElement('li');
    li.className = 'pl-item';
    li.draggable = true;
    li.dataset.idx = idx;

    // Aktuell spielendes Asset hervorheben
    var currentIdx = playbackIndex[monitorId];
    var isPlaying = (typeof currentIdx === 'number' && currentIdx === idx);

    if (isPlaying) {
      li.classList.add('pl-now-playing');
    }

    var numSpan = document.createElement('span');
    numSpan.className = 'pl-num';
    if (isPlaying) {
      numSpan.innerHTML = '&#9654;';
    } else {
      numSpan.textContent = (idx + 1) + '.';
    }

    var nameSpan = document.createElement('span');
    nameSpan.className = 'pl-name';
    nameSpan.textContent = item.asset;

    // Preview bei Mouseover
    if (item.uri) {
      var mimeGuess = guessType(item.uri);
      if (mimeGuess) {
        (function (uri, type) {
          li.addEventListener('mouseenter', function (e) {
            showPreviewTooltip(assetUrl(uri), type, e);
          });
          li.addEventListener('mousemove', positionTooltip);
          li.addEventListener('mouseleave', hidePreviewTooltip);
        })(item.uri, mimeGuess);
      }
    }

    var durInput = document.createElement('input');
    durInput.type = 'number';
    durInput.className = 'pl-dur-input';
    durInput.value = item.duration || 10;
    durInput.min = 1;
    durInput.max = 9999;
    durInput.title = 'Anzeigedauer in Sekunden';
    durInput.addEventListener('change', function () {
      item.duration = parseInt(durInput.value, 10) || 10;
      wallConfig.playlists[monitorId] = playlist;
      savePlaylist(monitorId);
      showOutput('Dauer: ' + item.asset + ' → ' + item.duration + 's');
    });

    var durLabel = document.createElement('span');
    durLabel.className = 'pl-dur-label';
    durLabel.textContent = 's';

    li.appendChild(numSpan);
    li.appendChild(nameSpan);
    li.appendChild(durInput);
    li.appendChild(durLabel);

    var removeBtn = document.createElement('button');
    removeBtn.className = 'pl-remove';
    removeBtn.textContent = '\u00d7';
    removeBtn.addEventListener('click', function () {
      playlist.splice(idx, 1);
      wallConfig.playlists[monitorId] = playlist;
      savePlaylist(monitorId);
      renderPlaylist(monitorId);
      renderCanvas();
    });
    li.appendChild(removeBtn);

    // Sortierung per Drag
    li.addEventListener('dragstart', function (e) {
      e.dataTransfer.setData('text/plain', JSON.stringify({ type: 'reorder', fromIdx: idx }));
    });

    li.addEventListener('dragover', function (e) {
      e.preventDefault();
      li.classList.add('drag-over');
    });

    li.addEventListener('dragleave', function () {
      li.classList.remove('drag-over');
    });

    li.addEventListener('drop', function (e) {
      e.preventDefault();
      li.classList.remove('drag-over');
      handleDrop(e, monitorId, idx);
    });

    list.appendChild(li);
  });
}

function handleDrop(e, monitorId, targetIdx) {
  var raw = e.dataTransfer.getData('text/plain');
  try { var data = JSON.parse(raw); } catch (_) { return; }

  var playlist = (wallConfig.playlists || {})[monitorId] || [];

  if (data.type === 'reorder') {
    var item = playlist.splice(data.fromIdx, 1)[0];
    playlist.splice(targetIdx, 0, item);
  } else if (data.type === 'pool') {
    playlist.splice(targetIdx + 1, 0, {
      asset_id: data.id,
      asset: data.name,
      duration: parseInt(data.duration, 10) || 10,
      uri: data.uri,
      mimetype: data.mimetype || '',
    });
    showOutput('Asset hinzugefuegt: ' + data.name + ' \u2192 ' + monitorId);
  }

  wallConfig.playlists[monitorId] = playlist;
  savePlaylist(monitorId);
  renderPlaylist(monitorId);
  renderCanvas();
}

/* Drop-Zone (Playlist-Ende) */
(function () {
  var zone = document.getElementById('playlistDropZone');

  zone.addEventListener('dragover', function (e) {
    e.preventDefault();
    zone.classList.add('drop-hover');
  });

  zone.addEventListener('dragleave', function () {
    zone.classList.remove('drop-hover');
  });

  zone.addEventListener('drop', function (e) {
    e.preventDefault();
    zone.classList.remove('drop-hover');
    if (!selectedMonitor || !wallConfig) {
      showOutput('Erst einen Monitor auswaehlen!');
      return;
    }

    var raw = e.dataTransfer.getData('text/plain');
    try { var data = JSON.parse(raw); } catch (_) { return; }
    if (data.type !== 'pool') return;

    if (!wallConfig.playlists) wallConfig.playlists = {};
    if (!wallConfig.playlists[selectedMonitor]) wallConfig.playlists[selectedMonitor] = [];

    wallConfig.playlists[selectedMonitor].push({
      asset_id: data.id,
      asset: data.name,
      duration: parseInt(data.duration, 10) || 10,
      uri: data.uri,
      mimetype: data.mimetype || '',
    });

    savePlaylist(selectedMonitor);
    renderPlaylist(selectedMonitor);
    renderCanvas();
    showOutput('Asset hinzugefuegt: ' + data.name + ' \u2192 ' + selectedMonitor);
  });
})();

/* ============================================
   DISPLAY SETTINGS
   ============================================ */

function renderDisplaySettings() {
  var grid = document.getElementById('displayGrid');
  grid.innerHTML = '';
  if (!wallConfig) return;

  // Monitore nach Pi gruppieren: head, slave1, slave2
  var pis = [
    { name: 'Head-Pi', prefix: 'head', monitors: [] },
    { name: 'Slave 1', prefix: 'slave1', monitors: [] },
    { name: 'Slave 2', prefix: 'slave2', monitors: [] },
  ];

  wallConfig.canvas.monitors.forEach(function (mon) {
    for (var i = 0; i < pis.length; i++) {
      if (mon.id.startsWith(pis[i].prefix)) {
        pis[i].monitors.push(mon);
        break;
      }
    }
  });

  pis.forEach(function (pi) {
    if (!pi.monitors.length) return;

    var card = document.createElement('div');
    card.className = 'pi-card';

    var header = '<div class="pi-card-header"><h3>' + pi.name + '</h3></div>';
    var displays = '';

    pi.monitors.forEach(function (mon) {
      var color = MONITOR_COLORS[mon.id] || '#972c2b';
      var pl = (wallConfig.playlists || {})[mon.id] || [];
      displays +=
        '<div class="pi-display" style="border-left-color:' + color + '">' +
          '<div class="pi-display-name">' + escHtml(mon.label || mon.id) +
            '<span class="pi-display-assets">' + pl.length + ' Assets</span></div>' +
          '<div class="field"><label>Output</label><span>' + mon.output + '</span></div>' +
          '<div class="field"><label>Rotation</label>' +
            '<select data-mon="' + mon.id + '" onchange="setRotation(this)">' +
              '<option value="0"' + (mon.rotation === 0 ? ' selected' : '') + '>0\u00b0 Landscape</option>' +
              '<option value="90"' + (mon.rotation === 90 ? ' selected' : '') + '>90\u00b0 Portrait</option>' +
              '<option value="180"' + (mon.rotation === 180 ? ' selected' : '') + '>180\u00b0</option>' +
              '<option value="270"' + (mon.rotation === 270 ? ' selected' : '') + '>270\u00b0 Portrait</option>' +
            '</select></div>' +
          '<div class="field"><label>Aufloesung</label><span>' + mon.width + '\u00d7' + mon.height + '</span></div>' +
        '</div>';
    });

    card.innerHTML = header + '<div class="pi-displays">' + displays + '</div>';
    grid.appendChild(card);
  });
}

function setRotation(sel) {
  var monId = sel.dataset.mon;
  var rotation = parseInt(sel.value, 10);
  var mon = findMonitor(monId);
  if (!mon) return;

  mon.rotation = rotation;

  saveWallConfig();
  renderCanvas();
  renderDisplaySettings();
  showOutput('Rotation: ' + monId + ' \u2192 ' + rotation + '\u00b0');
}

/* ============================================
   TOOLBAR COMMANDS
   ============================================ */

var transportState = 'stop';
var transportStateUserSet = false;

function cmdAll(cmd) {
  transportStateUserSet = true;
  if (cmd === 'play') {
    transportState = 'play';
  } else if (cmd === 'stop') {
    transportState = 'stop';
    Object.keys(playbackIndex).forEach(function (id) {
      playbackIndex[id] = 0;
    });
    if (selectedMonitor) {
      renderPlaylist(selectedMonitor);
      updateLivePreview(selectedMonitor);
    }
  }

  // Alle Befehle global an Head + Slaves senden
  fetch('/api/viewer/command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd: cmd, monitor: '' }),
  });

  updateTransportButtons();
  showOutput('Befehl: ' + cmd);
}

function updateTransportButtons() {
  var play = document.querySelector('.tb-play');
  var stop = document.querySelector('.tb-stop');

  if (play) play.classList.toggle('tb-active-play', transportState === 'play');
  if (stop) stop.classList.toggle('tb-active-stop', transportState === 'stop');
}

function updateToolbarStatus(status) {
  var d1 = document.getElementById('tbDot1');
  var d2 = document.getElementById('tbDot2');
  var temp = document.getElementById('tbTemp');
  if (d1) d1.className = 'tb-dot ' + (status.viewer1_running ? 'green' : 'red');
  if (d2) d2.className = 'tb-dot ' + (status.viewer2_running ? 'green' : 'red');

  // Transport-State nur beim ersten Load ableiten (nicht user-gesetzte States ueberschreiben)
  if (!transportStateUserSet && (status.viewer1_running || status.viewer2_running)) {
    transportState = 'play';
    updateTransportButtons();
    transportStateUserSet = true;
  }
  if (temp) {
    var t = (status.temperature || '').replace('temp=', '').replace("'C", '');
    temp.textContent = t ? t + '\u00b0C' : '';
  }
}

/* ============================================
   STATUS
   ============================================ */

function renderStatus(status, slaves) {
  var grid = document.getElementById('statusGrid');
  grid.innerHTML = '';

  // Head-Pi: Status in einheitliches Format bringen
  var headData = {
    hostname: status.hostname || 'head',
    ip: status.ip || '',
    temperature: status.temperature || '',
    throttle: status.throttle || '0x0',
    uptime: status.uptime || 'N/A',
    disk: status.disk || 'N/A',
    memory: status.memory || 'N/A',
    mac_wlan: status.mac_wlan || '',
    mac_eth: status.mac_eth || '',
    online: true,
    viewers: {
      'head-1': { running: status.viewer1_running, playlist_length: 0, asset: '' },
      'head-2': { running: status.viewer2_running, playlist_length: 0, asset: '' },
    },
  };
  // Playlist-Laengen aus wallConfig einfuegen
  if (wallConfig && wallConfig.playlists) {
    var h1 = wallConfig.playlists['head-1'] || [];
    var h2 = wallConfig.playlists['head-2'] || [];
    headData.viewers['head-1'].playlist_length = h1.length;
    headData.viewers['head-2'].playlist_length = h2.length;
    var idx1 = playbackIndex['head-1'];
    var idx2 = playbackIndex['head-2'];
    if (typeof idx1 === 'number' && h1[idx1]) headData.viewers['head-1'].asset = h1[idx1].asset;
    if (typeof idx2 === 'number' && h2[idx2]) headData.viewers['head-2'].asset = h2[idx2].asset;
  }
  grid.appendChild(buildDeviceCard(headData, 'head'));

  // Slave-Karten
  var slaveNames = ['slave1', 'slave2'];
  slaveNames.forEach(function (name) {
    var slaveData = (slaves || {})[name];
    if (slaveData && slaveData.online) {
      grid.appendChild(buildDeviceCard(slaveData, name));
    } else {
      var sc = document.createElement('div');
      sc.className = 'pi-card pi-offline';
      var errMsg = slaveData && slaveData.error ? slaveData.error : 'Pi noch nicht eingerichtet';
      var ipStr = slaveData && slaveData.ip ? slaveData.ip : 'Nicht verbunden';
      sc.innerHTML =
        '<div class="pi-card-header"><h3>' + name + '</h3><span class="pi-card-ip">' + ipStr + '</span></div>' +
        '<div class="empty-hint" style="padding:1rem">' + escHtml(errMsg) + '</div>';
      grid.appendChild(sc);
    }
  });
}

function buildDeviceCard(data, name) {
  var tempStr = (data.temperature || '').replace("temp=", "").replace("'C", "");
  var tempNum = parseFloat(tempStr) || 0;
  var tempOk = tempNum < 70;
  var tempWarn = tempNum >= 60 && tempNum < 70;
  var throttleOk = !data.throttle || data.throttle === '0x0';

  var card = document.createElement('div');
  card.className = 'pi-card';

  var tempDot = tempWarn ? 'yellow' : (tempOk ? 'green' : 'red');
  var thrDot = throttleOk ? 'green' : 'red';

  // Disk: einheitlich darstellen
  var diskStr = 'N/A';
  if (typeof data.disk === 'string') {
    diskStr = data.disk;
  } else if (data.disk && data.disk.total_gb !== undefined) {
    diskStr = data.disk.used_gb + '/' + data.disk.total_gb + ' GB (' + Math.round(data.disk.used_gb / data.disk.total_gb * 100) + '%)';
    if (data.disk.usb) diskStr += ' USB';
  }

  // Uptime + RAM (Slave-Agent liefert das noch nicht, daher fallback)
  var uptimeStr = data.uptime || '';
  var memStr = data.memory || '';

  // Viewer-Status
  var viewers = data.viewers || {};
  var v1 = viewers[name + '-1'] || {};
  var v2 = viewers[name + '-2'] || {};

  // HDMI-Farben je nach Pi
  var colors = {
    'head':   ['#e94560', '#ff6b6b'],
    'slave1': ['#4ecdc4', '#45b7aa'],
    'slave2': ['#f9ca24', '#f0b800'],
  };
  var c = colors[name] || ['#888', '#666'];

  card.innerHTML =
    '<div class="pi-card-header">' +
      '<h3>' + escHtml(data.hostname || name) + '</h3>' +
      '<span class="pi-card-ip">' + escHtml(data.ip || '') + '</span>' +
    '</div>' +
    '<div class="pi-status-grid">' +
      '<div class="pi-stat"><span class="status-dot ' + tempDot + '"></span>Temp<span class="pi-stat-val">' + tempStr + '\u00b0C</span></div>' +
      '<div class="pi-stat"><span class="status-dot ' + thrDot + '"></span>Throttle<span class="pi-stat-val">' + (throttleOk ? 'OK' : data.throttle) + '</span></div>' +
      (uptimeStr ? '<div class="pi-stat"><span class="status-dot green"></span>Uptime<span class="pi-stat-val">' + uptimeStr + '</span></div>' : '') +
      '<div class="pi-stat"><span class="status-dot green"></span>Disk<span class="pi-stat-val">' + diskStr + '</span></div>' +
      (memStr ? '<div class="pi-stat"><span class="status-dot green"></span>RAM<span class="pi-stat-val">' + memStr + '</span></div>' : '') +
    '</div>' +
    '<div class="pi-displays">' +
      '<div class="pi-display" style="border-left-color:' + c[0] + '">' +
        '<div class="pi-display-name">HDMI-1' +
          '<span class="status-dot ' + (v1.running ? 'green' : 'red') + '" style="margin-left:0.5rem"></span>' +
          '<span class="pi-stat-val" style="margin-left:0.5rem">' + (v1.playlist_length || 0) + ' Assets</span>' +
        '</div>' +
        (v1.asset ? '<div class="pi-display-asset">' + escHtml(v1.asset) + '</div>' : '') +
      '</div>' +
      '<div class="pi-display" style="border-left-color:' + c[1] + '">' +
        '<div class="pi-display-name">HDMI-2' +
          '<span class="status-dot ' + (v2.running ? 'green' : 'red') + '" style="margin-left:0.5rem"></span>' +
          '<span class="pi-stat-val" style="margin-left:0.5rem">' + (v2.playlist_length || 0) + ' Assets</span>' +
        '</div>' +
        (v2.asset ? '<div class="pi-display-asset">' + escHtml(v2.asset) + '</div>' : '') +
      '</div>' +
    '</div>' +
    (data.mac_wlan || data.mac_eth ?
      '<div class="pi-meta">' +
        (data.mac_wlan ? '<div>MAC WLAN: ' + data.mac_wlan + '</div>' : '') +
        (data.mac_eth ? '<div>MAC ETH: ' + data.mac_eth + '</div>' : '') +
      '</div>' : '');

  return card;
}

/* ============================================
   INIT
   ============================================ */

/* ============================================
   FILE UPLOAD
   ============================================ */

function uploadFile(file) {
  var formData = new FormData();
  formData.append('file', file);
  formData.append('filename', file.name);
  formData.append('duration', '10');

  showOutput('Lade hoch: ' + file.name + '...');

  fetch('/api/upload', { method: 'POST', body: formData })
    .then(function (res) { return res.json(); })
    .then(function (data) {
      if (data.ok) {
        showOutput('Hochgeladen: ' + file.name);
        loadAssets();
      } else {
        showOutput('Fehler: ' + (data.error || 'Upload fehlgeschlagen'));
      }
    })
    .catch(function (e) {
      showOutput('Upload-Fehler: ' + e.message);
    });
}

// Datei-Input
document.getElementById('fileInput').addEventListener('change', function (e) {
  Array.from(e.target.files).forEach(uploadFile);
  e.target.value = '';
});

// Drag&Drop auf Upload-Zone
(function () {
  var drop = document.getElementById('uploadDrop');
  var sidebar = document.getElementById('poolSidebar');

  // Prevent browser default (open file in tab)
  sidebar.addEventListener('dragover', function (e) {
    // Nur File-Drops abfangen, nicht interne Pool-Drags
    if (e.dataTransfer.types.indexOf('Files') !== -1) {
      e.preventDefault();
      drop.classList.add('drag-over');
    }
  });

  sidebar.addEventListener('dragleave', function (e) {
    if (!sidebar.contains(e.relatedTarget)) {
      drop.classList.remove('drag-over');
    }
  });

  sidebar.addEventListener('drop', function (e) {
    drop.classList.remove('drag-over');
    if (e.dataTransfer.files && e.dataTransfer.files.length) {
      e.preventDefault();
      Array.from(e.dataTransfer.files).forEach(uploadFile);
    }
  });
})();

/* ============================================
   INIT
   ============================================ */

/* ============================================
   ASSET HELPERS
   ============================================ */

function guessType(uri) {
  if (!uri) return null;
  var lower = uri.toLowerCase();
  if (/\.(jpe?g|png|gif|bmp|webp|svg)/.test(lower)) return 'image';
  if (/\.(mp4|webm|mov|avi|mkv)/.test(lower)) return 'video';
  return null;
}

function assetUrl(uri) {
  // Absolute Pfade (z.B. /home/head/screenly_assets/UUID.jpg) in Browser-URL umwandeln
  if (!uri) return uri;
  var match = uri.match(/screenly_assets\/(.+)$/);
  if (match) return '/assets/' + match[1];
  // Docker-Pfade
  var match2 = uri.match(/\/data\/screenly_assets\/(.+)$/);
  if (match2) return '/assets/' + match2[1];
  return uri;
}

function thumbUrl(uri) {
  // Thumbnail-URL: /thumb/ statt /assets/
  if (!uri) return uri;
  var match = uri.match(/screenly_assets\/(.+)$/);
  if (match) return '/thumb/' + match[1];
  var match2 = uri.match(/\/data\/screenly_assets\/(.+)$/);
  if (match2) return '/thumb/' + match2[1];
  return uri;
}

/* ============================================
   PREVIEW TOOLTIP
   ============================================ */

var previewEl = null;

function showPreviewTooltip(uri, type, event) {
  hidePreviewTooltip();
  if (!uri) return;

  previewEl = document.createElement('div');
  previewEl.className = 'preview-tooltip';

  if (type === 'image') {
    var img = document.createElement('img');
    img.src = uri.replace('/assets/', '/thumb/');
    img.alt = 'Preview';
    previewEl.appendChild(img);
  } else if (type === 'video') {
    var vid = document.createElement('video');
    vid.src = uri;
    vid.muted = true;
    vid.autoplay = true;
    vid.loop = true;
    vid.playsInline = true;
    previewEl.appendChild(vid);
  }

  document.body.appendChild(previewEl);
  positionTooltip(event);
}

function positionTooltip(e) {
  if (!previewEl) return;
  var x = e.clientX - 220;
  var y = e.clientY - 10;
  if (x < 10) x = e.clientX + 20;
  if (y < 10) y = 10;
  previewEl.style.left = x + 'px';
  previewEl.style.top = y + 'px';
}

function hidePreviewTooltip() {
  if (previewEl) {
    // Video stoppen
    var vid = previewEl.querySelector('video');
    if (vid) { vid.pause(); vid.src = ''; }
    previewEl.remove();
    previewEl = null;
  }
}

/* ============================================
   PLAYBACK-TRACKER (Server-synchronisiert)
   ============================================ */

var playbackIndex = {};   // pro Monitor: aktueller Index

async function syncPlaybackState() {
  try {
    var res = await fetch('/api/playback');
    var state = await res.json();
    // state ist z.B. {"head-1": {"index": 2, "asset": "foo.png"}, ...}
    var changed = false;
    for (var monitorId in state) {
      var newIdx = state[monitorId].index;
      if (typeof newIdx === 'number' && playbackIndex[monitorId] !== newIdx) {
        playbackIndex[monitorId] = newIdx;
        changed = true;
      }
    }
    if (changed && selectedMonitor) {
      renderPlaylist(selectedMonitor);
      updateLivePreview(selectedMonitor);
    }
  } catch (e) {
    // Viewer laeuft nicht oder kein Netz — ignorieren
  }
}

function startPlaybackTracker(monitorId) {
  // Nichts zu tun — Sync laeuft ueber syncPlaybackState-Polling
}

function stopPlaybackTracker(monitorId) {
  // Nichts zu tun — Sync laeuft ueber syncPlaybackState-Polling
}

function jumpPlayback(monitorId, direction) {
  var cmd = direction > 0 ? 'next' : 'prev';
  fetch('/api/viewer/command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd: cmd, monitor: monitorId }),
  });
  // Preview aktualisiert sich automatisch via syncPlaybackState
}

function startAllTrackers() {
  // Nichts zu tun — Sync laeuft ueber syncPlaybackState-Polling
}

/* ============================================
   LIVE-PREVIEW
   ============================================ */

function updateLivePreview(monitorId) {
  var content = document.getElementById('livePreviewContent');
  var nameEl = document.getElementById('livePreviewName');
  if (!content || !nameEl) return;

  if (!monitorId || !wallConfig || !wallConfig.playlists) {
    content.innerHTML = '<div class="live-preview-placeholder">Monitor waehlen</div>';
    nameEl.textContent = '\u2014';
    return;
  }

  var pl = wallConfig.playlists[monitorId] || [];
  var idx = playbackIndex[monitorId];
  if (typeof idx !== 'number' || !pl.length) {
    content.innerHTML = '<div class="live-preview-placeholder">Playlist leer</div>';
    nameEl.textContent = '\u2014';
    return;
  }

  var item = pl[idx];
  if (!item) return;

  nameEl.textContent = item.asset;

  var url = assetUrl(item.uri);
  var type = guessType(item.uri);

  // Rotation des Monitors ermitteln
  var rotation = 0;
  if (wallConfig && wallConfig.canvas && wallConfig.canvas.monitors) {
    for (var mi = 0; mi < wallConfig.canvas.monitors.length; mi++) {
      if (wallConfig.canvas.monitors[mi].id === monitorId) {
        rotation = parseInt(wallConfig.canvas.monitors[mi].rotation, 10) || 0;
        break;
      }
    }
  }

  // HTML mit Rotation direkt inline erzeugen
  var imgStyle = rotation
    ? 'style="width:100%;height:100%;object-fit:contain;border-radius:4px"'
    : '';

  if (type === 'image') {
    var src = thumbUrl(item.uri);
    if (rotation) {
      content.innerHTML =
        '<div style="width:160px;height:160px;margin:0 auto;transform:rotate(' + rotation + 'deg)">' +
          '<img src="' + src + '" ' + imgStyle + '>' +
        '</div>';
    } else {
      content.innerHTML = '<img src="' + src + '" alt="' + escHtml(item.asset) + '">';
    }
  } else if (type === 'video') {
    if (rotation) {
      content.innerHTML =
        '<div style="width:160px;height:160px;margin:0 auto;transform:rotate(' + rotation + 'deg)">' +
          '<video src="' + url + '" muted autoplay loop playsinline ' + imgStyle + '></video>' +
        '</div>';
    } else {
      content.innerHTML = '<video src="' + url + '" muted autoplay loop playsinline></video>';
    }
  } else {
    content.innerHTML = '<div class="live-preview-placeholder">' + escHtml(item.asset) + '</div>';
  }
}

/* ============================================
   INIT
   ============================================ */

loadAssets();
loadWallConfig();
loadStatus();
setInterval(loadStatus, 5000);
setInterval(loadAssets, 15000);
syncPlaybackState();
setInterval(syncPlaybackState, 500);
