/* Displaywall VJ-Manager — Fabric.js Canvas */

var canvas = null;
var SCALE = 0.08;
var PAD = 15;
var canvasMode = 'select';

var COLORS = {
  'head-1': '#e94560', 'head-2': '#ff6b6b',
  'slave1-1': '#4ecdc4', 'slave1-2': '#45b7aa',
  'slave2-1': '#f9ca24', 'slave2-2': '#f0b800',
};

function setCanvasMode(mode) {
  // Beim Verlassen von Arrange: Positionen aus Canvas lesen und speichern
  if (canvasMode === 'arrange' && mode !== 'arrange') {
    savePositionsFromCanvas();
  }

  canvasMode = mode;
  document.getElementById('btnModeSelect').classList.toggle('active', mode === 'select');
  document.getElementById('btnModeArrange').classList.toggle('active', mode === 'arrange');

  var wrap = document.getElementById('canvasWrap');

  if (mode === 'arrange') {
    document.getElementById('canvasModeHint').textContent =
      'Monitore verschieben, dann "Auswaehlen" klicken zum Fixieren';
    wrap.classList.add('arrange-active');
    wrap.classList.remove('select-compact');
  } else {
    document.getElementById('canvasModeHint').textContent =
      'Klick = Monitor waehlen';
    wrap.classList.remove('arrange-active');
    wrap.classList.add('select-compact');
    // Im Select-Modus: Canvas auf kompakte Groesse setzen
    resizeCanvas();
  }

  renderCanvas();
}

function savePositionsFromCanvas() {
  if (!canvas || !wallConfig) return;
  var bounds = getMonitorBounds();
  canvas.getObjects().forEach(function (obj) {
    if (!obj.monitorId) return;
    var mon = findMonitor(obj.monitorId);
    if (mon) {
      mon.x = Math.round((obj.left - PAD) / SCALE + bounds.minX);
      mon.y = Math.round((obj.top - PAD) / SCALE + bounds.minY);
    }
  });
  saveWallConfig();
  showOutput('Anordnung gespeichert');
}

function initCanvas() {
  if (canvas) return;

  var wrap = document.getElementById('canvasWrap');
  var w = wrap.clientWidth - 2;
  if (w < 300) w = 600;
  var h = Math.round(w * 0.45);

  canvas = new fabric.Canvas('wallCanvas', {
    width: w,
    height: h,
    backgroundColor: '#111118',
    selection: false,
  });

  // Klick: im Select-Modus Monitor waehlen
  canvas.on('mouse:up', function (e) {
    if (canvasMode !== 'select') return;
    if (!e.target || !e.target.monitorId) return;
    selectMonitor(e.target.monitorId);
    renderCanvas();
  });

  // --- HTML5 Drag&Drop (Pool → Canvas) ---
  setupCanvasDrop(wrap);
}

function setupCanvasDrop(el) {
  el.addEventListener('dragover', function (e) {
    e.preventDefault();
    var monId = getMonitorAtXY(e);
    highlightDrop(monId);
  });

  el.addEventListener('dragleave', function () {
    highlightDrop(null);
  });

  el.addEventListener('drop', function (e) {
    e.preventDefault();
    highlightDrop(null);

    var monId = getMonitorAtXY(e);
    if (!monId) {
      showOutput('Kein Monitor getroffen');
      return;
    }

    var raw = e.dataTransfer.getData('text/plain');
    try { var data = JSON.parse(raw); } catch (_) { return; }
    if (data.type !== 'pool') return;

    if (!wallConfig.playlists) wallConfig.playlists = {};
    if (!wallConfig.playlists[monId]) wallConfig.playlists[monId] = [];

    wallConfig.playlists[monId].push({
      asset_id: data.id,
      asset: data.name,
      duration: parseInt(data.duration, 10) || 10,
      uri: data.uri,
      mimetype: data.mimetype || '',
    });

    savePlaylist(monId);
    selectMonitor(monId);
    renderCanvas();
    showOutput(data.name + ' \u2192 ' + monId + ' (gespeichert)');
  });

}


function getMonitorAtXY(e) {
  if (!canvas || !wallConfig) return null;
  var rect = canvas.lowerCanvasEl.getBoundingClientRect();
  var x = e.clientX - rect.left;
  var y = e.clientY - rect.top;

  var bounds = getMonitorBounds();
  for (var i = wallConfig.canvas.monitors.length - 1; i >= 0; i--) {
    var mon = wallConfig.canvas.monitors[i];
    var isPortrait = (mon.rotation === 90 || mon.rotation === 270);
    var mw = (isPortrait ? mon.height : mon.width) * SCALE;
    var mh = (isPortrait ? mon.width : mon.height) * SCALE;
    var mx = (mon.x - bounds.minX) * SCALE + PAD;
    var my = (mon.y - bounds.minY) * SCALE + PAD;

    if (x >= mx && x <= mx + mw && y >= my && y <= my + mh) {
      return mon.id;
    }
  }
  return null;
}

function highlightDrop(monId) {
  if (!canvas) return;
  canvas.getObjects().forEach(function (obj) {
    if (!obj.monitorId) return;
    var items = obj.getObjects ? obj.getObjects() : [];
    var r = items.length ? items[0] : obj;
    if (obj.monitorId === monId) {
      r.set({ opacity: 0.7, strokeWidth: 3, stroke: '#ffffff' });
    } else {
      var isSel = (selectedMonitor === obj.monitorId);
      r.set({
        opacity: isSel ? 0.5 : 0.2,
        strokeWidth: isSel ? 3 : 2,
        stroke: isSel ? '#ffffff' : COLORS[obj.monitorId] || '#888',
      });
    }
  });
  canvas.requestRenderAll();
}

function resizeCanvas() {
  if (!canvas) initCanvas();
  if (!canvas) return;

  var wrap = document.getElementById('canvasWrap');
  if (!wrap) return;

  var w = wrap.clientWidth - 2;
  if (w < 300) w = 600;

  if (wallConfig && wallConfig.canvas) {
    var bounds = getMonitorBounds();
    var contentW = bounds.maxX - bounds.minX;
    var contentH = bounds.maxY - bounds.minY;

    if (contentW > 0 && contentH > 0) {
      // Maximale Hoehe: 60% der Viewport-Hoehe oder 500px
      var maxH = Math.min(Math.round(window.innerHeight * 0.6), 500);
      var scaleW = (w - PAD * 2) / contentW;
      var scaleH = (maxH - PAD * 2) / contentH;
      SCALE = Math.min(scaleW, scaleH);

      var h = Math.round(contentH * SCALE + PAD * 2);
      if (h < 120) h = 120;

      canvas.setWidth(w);
      canvas.setHeight(h);
    } else {
      canvas.setWidth(w);
      canvas.setHeight(Math.round(w * 0.45));
      SCALE = (w - PAD * 2) / (wallConfig.canvas.width || 8000);
    }
  } else {
    canvas.setWidth(w);
    canvas.setHeight(Math.round(w * 0.45));
  }
}

function getMonitorBounds() {
  // Berechnet die Bounding-Box aller Monitore in Pixel-Koordinaten
  var minX = Infinity, minY = Infinity, maxX = 0, maxY = 0;

  if (!wallConfig || !wallConfig.canvas) return { minX: 0, minY: 0, maxX: 8000, maxY: 3000 };

  wallConfig.canvas.monitors.forEach(function (mon) {
    var isPortrait = (mon.rotation === 90 || mon.rotation === 270);
    var mw = isPortrait ? mon.height : mon.width;
    var mh = isPortrait ? mon.width : mon.height;

    if (mon.x < minX) minX = mon.x;
    if (mon.y < minY) minY = mon.y;
    if (mon.x + mw > maxX) maxX = mon.x + mw;
    if (mon.y + mh > maxY) maxY = mon.y + mh;
  });

  return { minX: minX, minY: minY, maxX: maxX, maxY: maxY };
}

function renderCanvas() {
  if (!canvas || !wallConfig) return;

  canvas.clear();

  var isArrange = (canvasMode === 'arrange');
  canvas.selection = isArrange;

  var bounds = getMonitorBounds();

  wallConfig.canvas.monitors.forEach(function (mon) {
    var color = COLORS[mon.id] || '#888';
    var isPortrait = (mon.rotation === 90 || mon.rotation === 270);
    var mw = (isPortrait ? mon.height : mon.width) * SCALE;
    var mh = (isPortrait ? mon.width : mon.height) * SCALE;
    var isSel = (selectedMonitor === mon.id);
    var pl = (wallConfig.playlists || {})[mon.id] || [];
    var posX = (mon.x - bounds.minX) * SCALE + PAD;
    var posY = (mon.y - bounds.minY) * SCALE + PAD;

    // Rotation-Symbol
    var rotSymbol = '';
    if (mon.rotation === 90) rotSymbol = ' \u21bb';
    else if (mon.rotation === 270) rotSymbol = ' \u21ba';
    else if (mon.rotation === 180) rotSymbol = ' \u21c5';

    var fontSize = Math.max(9, Math.min(13, mw * 0.065));

    // Hintergrund
    var rect = new fabric.Rect({
      width: mw,
      height: mh,
      fill: color,
      opacity: isSel ? 0.5 : 0.2,
      stroke: isSel ? '#ffffff' : color,
      strokeWidth: isSel ? 3 : 2,
      rx: 4,
      ry: 4,
    });

    // Label
    var label = new fabric.Text(
      (mon.label || mon.id) + rotSymbol,
      {
        fontSize: fontSize,
        fill: '#fff',
        fontWeight: '700',
        fontFamily: 'system-ui, sans-serif',
        originX: 'center',
        originY: 'center',
        left: mw / 2,
        top: mh * 0.3,
      }
    );

    // Info
    var rotText = mon.rotation ? mon.rotation + '\u00b0' : '0\u00b0';
    var info = new fabric.Text(
      mon.output + ' | ' + rotText + '\n' + pl.length + ' Assets',
      {
        fontSize: Math.max(7, fontSize * 0.65),
        fill: '#bbb',
        fontFamily: 'system-ui, sans-serif',
        originX: 'center',
        originY: 'center',
        left: mw / 2,
        top: mh * 0.68,
        textAlign: 'center',
        lineHeight: 1.4,
      }
    );

    // Group: alles bewegt sich zusammen
    rect._group = null; // Wird nach group-Erstellung gesetzt
    var group = new fabric.Group([rect, label, info], {
      left: posX,
      top: posY,
      selectable: isArrange,
      evented: true,
      hasControls: false,
      hasBorders: isArrange,
      borderColor: '#ffffff',
      lockRotation: true,
      lockScalingX: true,
      lockScalingY: true,
      hoverCursor: isArrange ? 'grab' : 'pointer',
      moveCursor: 'grabbing',
      subTargetCheck: false,
    });

    group.monitorId = mon.id;
    rect._group = group;
    canvas.add(group);
  });

  canvas.requestRenderAll();
}

// ResizeObserver: Im Arrange-Modus nur Canvas-Dimensionen anpassen,
// SCALE und Objekt-Positionen bleiben unveraendert
(function () {
  var wrap = document.getElementById('canvasWrap');
  if (!wrap || !window.ResizeObserver) return;

  new ResizeObserver(function () {
    if (!canvas || canvasMode !== 'arrange') return;
    var w = wrap.clientWidth;
    var h = wrap.clientHeight;
    if (w < 100 || h < 100) return;

    canvas.setWidth(w);
    canvas.setHeight(h);
    canvas.requestRenderAll();
  }).observe(wrap);
})();

window.addEventListener('resize', function () {
  if (canvasMode !== 'arrange') {
    resizeCanvas();
  }
  renderCanvas();
});
