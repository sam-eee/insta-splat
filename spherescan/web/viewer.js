// A small, dependency-free WebGL2 viewer for the .glb that Spirula writes.
// Handles: embedded textures (png/jpg), vertex colours, node transforms, 16/32-bit
// indices, quantised attributes. Does not handle: Draco/meshopt compression, animation.
(function () {
  "use strict";
  const SIZES = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4};
  const COMPS = {SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4};
  const NORM = {5120: 127, 5121: 255, 5122: 32767, 5123: 65535};

  const m4 = {
    id: () => new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]),
    mul(a, b) { const o = new Float32Array(16);
      for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
        let s = 0; for (let k = 0; k < 4; k++) s += a[k*4 + r] * b[c*4 + k]; o[c*4 + r] = s; }
      return o; },
    trs(t, q, s) { const [x, y, z, w] = q, [sx, sy, sz] = s;
      return new Float32Array([
        (1 - 2*(y*y + z*z))*sx, 2*(x*y + z*w)*sx, 2*(x*z - y*w)*sx, 0,
        2*(x*y - z*w)*sy, (1 - 2*(x*x + z*z))*sy, 2*(y*z + x*w)*sy, 0,
        2*(x*z + y*w)*sz, 2*(y*z - x*w)*sz, (1 - 2*(x*x + y*y))*sz, 0,
        t[0], t[1], t[2], 1]); },
    persp(fov, asp, n, f) { const t = 1 / Math.tan(fov / 2), o = new Float32Array(16);
      o[0] = t / asp; o[5] = t; o[10] = (f + n) / (n - f); o[11] = -1; o[14] = 2 * f * n / (n - f); return o; },
    rotX(a) { const c = Math.cos(a), s = Math.sin(a); return new Float32Array([1,0,0,0, 0,c,s,0, 0,-s,c,0, 0,0,0,1]); },
    rotY(a) { const c = Math.cos(a), s = Math.sin(a); return new Float32Array([c,0,-s,0, 0,1,0,0, s,0,c,0, 0,0,0,1]); },
    tr(x, y, z) { const o = m4.id(); o[12] = x; o[13] = y; o[14] = z; return o; },
    pt(m, p) { return [m[0]*p[0] + m[4]*p[1] + m[8]*p[2] + m[12], m[1]*p[0] + m[5]*p[1] + m[9]*p[2] + m[13], m[2]*p[0] + m[6]*p[1] + m[10]*p[2] + m[14]]; },
  };

  function parseGlb(buf) {
    const dv = new DataView(buf);
    if (dv.getUint32(0, true) !== 0x46546C67) throw new Error("This is not a .glb file.");
    let off = 12, json = null, bin = null;
    while (off + 8 <= dv.byteLength) {
      const len = dv.getUint32(off, true), type = dv.getUint32(off + 4, true), start = off + 8;
      if (type === 0x4E4F534A) json = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, start, len)));
      else if (type === 0x004E4942 && bin === null) bin = start;
      off = start + len + (4 - len % 4) % 4;
    }
    if (!json) throw new Error("The model file is damaged.");
    const bad = (json.extensionsRequired || []).filter(e => !["KHR_materials_unlit", "KHR_texture_transform", "KHR_mesh_quantization"].includes(e));
    if (bad.length) throw new Error("This model uses features the built-in viewer cannot show (" + bad.join(", ") + "). Open the file in another 3D viewer.");
    return {json, bin: bin || 0};
  }

  function readAccessor(buf, g, idx, asIndex) {
    const a = g.json.accessors[idx], bv = g.json.bufferViews[a.bufferView];
    const n = COMPS[a.type], size = SIZES[a.componentType], stride = bv.byteStride || n * size;
    const base = g.bin + (bv.byteOffset || 0) + (a.byteOffset || 0);
    const dv = new DataView(buf);
    const get = {5120: "getInt8", 5121: "getUint8", 5122: "getInt16", 5123: "getUint16", 5125: "getUint32", 5126: "getFloat32"}[a.componentType];
    if (asIndex) {
      const out = new Uint32Array(a.count);
      for (let i = 0; i < a.count; i++) out[i] = dv[get](base + i * stride, true);
      return out;
    }
    if (a.componentType === 5126 && stride === n * 4) return {data: new Float32Array(buf.slice(base, base + a.count * n * 4)), n, count: a.count, acc: a};
    const out = new Float32Array(a.count * n), k = a.normalized ? NORM[a.componentType] : 0;
    for (let i = 0; i < a.count; i++) for (let c = 0; c < n; c++) {
      let v = dv[get](base + i * stride + c * size, true);
      if (k) v = Math.max(v / k, -1);
      out[i * n + c] = v;
    }
    return {data: out, n, count: a.count, acc: a};
  }

  function nodeMatrix(node) {
    if (node.matrix) return new Float32Array(node.matrix);
    return m4.trs(node.translation || [0, 0, 0], node.rotation || [0, 0, 0, 1], node.scale || [1, 1, 1]);
  }

  const VS = `#version 300 es
in vec3 aPos; in vec2 aUv; in vec4 aCol; uniform mat4 uMvp;
out vec2 vUv; out vec4 vCol; void main(){ vUv = aUv; vCol = aCol; gl_Position = uMvp * vec4(aPos, 1.0); }`;
  const FS = `#version 300 es
precision highp float; in vec2 vUv; in vec4 vCol; uniform sampler2D uTex; uniform bool uHasTex; uniform vec4 uBase; out vec4 o;
void main(){ vec4 c = uHasTex ? texture(uTex, vUv) : vCol; o = vec4((c * uBase).rgb, 1.0); }`;

  function Viewer(canvas) {
    const gl = canvas.getContext("webgl2", {antialias: true, alpha: false});
    if (!gl) throw new Error("Your browser cannot show 3D models here. Open the file in a 3D viewer instead.");
    this.canvas = canvas; this.gl = gl; this.draws = []; this.flip = false;
    const sh = (t, s) => { const o = gl.createShader(t); gl.shaderSource(o, s); gl.compileShader(o);
      if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(o)); return o; };
    const p = gl.createProgram(); gl.attachShader(p, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, FS));
    gl.bindAttribLocation(p, 0, "aPos"); gl.bindAttribLocation(p, 1, "aUv"); gl.bindAttribLocation(p, 2, "aCol");
    gl.linkProgram(p); this.prog = p;
    this.u = {mvp: gl.getUniformLocation(p, "uMvp"), tex: gl.getUniformLocation(p, "uTex"), has: gl.getUniformLocation(p, "uHasTex"), base: gl.getUniformLocation(p, "uBase")};
    this.dirty = true; this.resetView();
    this._bind(); new ResizeObserver(() => { this.dirty = true; }).observe(canvas);
    const loop = () => { if (this.dirty && !this.dead) { this.dirty = false; this._draw(); } if (!this.dead) requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }

  Viewer.prototype.resetView = function () { this.yaw = 0.6; this.pitch = 0.35; this.dist = 1.8; this.pan = [0, 0]; this.dirty = true; };
  Viewer.prototype.toggleFlip = function () { this.flip = !this.flip; this.dirty = true; };

  Viewer.prototype._bind = function () {
    const c = this.canvas; let mode = null, lx = 0, ly = 0, pinch = 0;
    c.style.touchAction = "none";
    c.addEventListener("contextmenu", e => e.preventDefault());
    c.addEventListener("pointerdown", e => { mode = (e.button === 2 || e.shiftKey) ? "pan" : "rot"; lx = e.clientX; ly = e.clientY; c.setPointerCapture(e.pointerId); });
    c.addEventListener("pointerup", () => { mode = null; });
    c.addEventListener("pointermove", e => {
      if (!mode || pinch) return; const dx = e.clientX - lx, dy = e.clientY - ly; lx = e.clientX; ly = e.clientY;
      if (mode === "rot") { this.yaw += dx * 0.008; this.pitch = Math.max(-1.55, Math.min(1.55, this.pitch + dy * 0.008)); }
      else { const k = this.dist * 0.0016; this.pan[0] -= dx * k; this.pan[1] += dy * k; }
      this.dirty = true; });
    c.addEventListener("wheel", e => { e.preventDefault(); this.dist = Math.max(0.2, Math.min(20, this.dist * Math.exp(e.deltaY * 0.0012))); this.dirty = true; }, {passive: false});
    c.addEventListener("touchmove", e => { if (e.touches.length === 2) { const d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
      if (pinch) this.dist = Math.max(0.2, Math.min(20, this.dist * pinch / d)); pinch = d; this.dirty = true; } }, {passive: true});
    c.addEventListener("touchend", () => { pinch = 0; });
  };

  Viewer.prototype.load = async function (buf, onStatus) {
    const g = parseGlb(buf), gl = this.gl, J = g.json;
    const scene = (J.scenes || [{nodes: (J.nodes || []).map((_, i) => i)}])[J.scene || 0];
    const prims = [];
    const walk = (i, parent) => {
      const node = J.nodes[i], world = m4.mul(parent, nodeMatrix(node));
      if (node.mesh !== undefined) for (const pr of J.meshes[node.mesh].primitives) prims.push({pr, world});
      (node.children || []).forEach(ch => walk(ch, world));
    };
    scene.nodes.forEach(i => walk(i, m4.id()));
    if (!prims.length) throw new Error("The model file has no 3D shapes in it.");

    const mn = [1e30, 1e30, 1e30], mx = [-1e30, -1e30, -1e30], texCache = new Map();
    let tris = 0;
    for (const {pr, world} of prims) {
      if (pr.mode !== undefined && pr.mode !== 4) continue;
      onStatus && onStatus("Preparing the model…");
      const pos = readAccessor(buf, g, pr.attributes.POSITION);
      const idx = pr.indices !== undefined ? readAccessor(buf, g, pr.indices, true) : Uint32Array.from({length: pos.count}, (_, i) => i);
      const uv = pr.attributes.TEXCOORD_0 !== undefined ? readAccessor(buf, g, pr.attributes.TEXCOORD_0) : null;
      const col = pr.attributes.COLOR_0 !== undefined ? readAccessor(buf, g, pr.attributes.COLOR_0) : null;
      const a = pos.acc, lo = a.min || [0, 0, 0], hi = a.max || [0, 0, 0];
      if (!a.min) { for (let i = 0; i < pos.count; i++) for (let c = 0; c < 3; c++) { const v = pos.data[i*3 + c]; lo[c] = Math.min(lo[c], v); hi[c] = Math.max(hi[c], v); } }
      for (const x of [lo[0], hi[0]]) for (const y of [lo[1], hi[1]]) for (const z of [lo[2], hi[2]]) {
        const w = m4.pt(world, [x, y, z]); for (let c = 0; c < 3; c++) { mn[c] = Math.min(mn[c], w[c]); mx[c] = Math.max(mx[c], w[c]); } }

      const vao = gl.createVertexArray(); gl.bindVertexArray(vao);
      const buffer = (data, loc, n) => { const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
        gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, n, gl.FLOAT, false, 0, 0); };
      buffer(pos.data, 0, 3);
      if (uv) buffer(uv.data, 1, 2); else gl.vertexAttrib2f(1, 0, 0);
      if (col) buffer(col.n === 3 ? Float32Array.from({length: col.count * 4}, (_, i) => i % 4 === 3 ? 1 : col.data[Math.floor(i / 4) * 3 + i % 4]) : col.data, 2, 4);
      else gl.vertexAttrib4f(2, 0.8, 0.8, 0.8, 1);
      const ib = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);

      let tex = null, base = [1, 1, 1, 1];
      const mat = pr.material !== undefined ? J.materials[pr.material] : null, pbr = mat && mat.pbrMetallicRoughness;
      if (pbr && pbr.baseColorFactor) base = pbr.baseColorFactor;
      if (pbr && pbr.baseColorTexture && uv) {
        const ti = pbr.baseColorTexture.index, src = J.textures[ti].source;
        if (!texCache.has(src)) {
          const im = J.images[src]; let blob = null;
          if (im.bufferView !== undefined) { const bv = J.bufferViews[im.bufferView];
            blob = new Blob([buf.slice(g.bin + (bv.byteOffset || 0), g.bin + (bv.byteOffset || 0) + bv.byteLength)], {type: im.mimeType || "image/png"}); }
          else if (im.uri) blob = await (await fetch(im.uri)).blob();
          const bmp = await createImageBitmap(blob, {imageOrientation: "none", premultiplyAlpha: "none"});
          const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
          gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, bmp);
          gl.generateMipmap(gl.TEXTURE_2D);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
          texCache.set(src, t);
        }
        tex = texCache.get(src);
      }
      this.draws.push({vao, count: idx.length, tex, base, world});
      tris += idx.length / 3;
    }
    gl.bindVertexArray(null);
    this.center = [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2];
    this.radius = Math.max(1e-6, 0.5 * Math.hypot(mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]));
    this.dirty = true;
    return {triangles: tris};
  };

  Viewer.prototype._draw = function () {
    const gl = this.gl, c = this.canvas, r = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(c.clientWidth * r)), h = Math.max(1, Math.round(c.clientHeight * r));
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    gl.viewport(0, 0, w, h); gl.clearColor(0.09, 0.1, 0.12, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    if (!this.draws.length) return;
    gl.enable(gl.DEPTH_TEST); gl.disable(gl.CULL_FACE); gl.useProgram(this.prog);
    const R = this.radius, d = this.dist * R * 1.45;
    const proj = m4.persp(0.8, w / h, Math.max(R * 0.01, d - R * 4), d + R * 4);
    let view = m4.mul(m4.tr(this.pan[0] * R, this.pan[1] * R, -d), m4.mul(m4.rotX(this.pitch), m4.rotY(this.yaw)));
    if (this.flip) view = m4.mul(view, m4.rotX(Math.PI));
    view = m4.mul(view, m4.tr(-this.center[0], -this.center[1], -this.center[2]));
    const vp = m4.mul(proj, view);
    for (const dr of this.draws) {
      gl.uniformMatrix4fv(this.u.mvp, false, m4.mul(vp, dr.world));
      gl.uniform4fv(this.u.base, dr.base); gl.uniform1i(this.u.has, dr.tex ? 1 : 0);
      if (dr.tex) { gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, dr.tex); gl.uniform1i(this.u.tex, 0); }
      gl.bindVertexArray(dr.vao); gl.drawElements(gl.TRIANGLES, dr.count, gl.UNSIGNED_INT, 0);
    }
    gl.bindVertexArray(null);
  };

  Viewer.prototype.dispose = function () { this.dead = true; };

  window.SphereViewer = Viewer;
})();
