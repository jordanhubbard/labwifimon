/**
 * particles.js — Particle animation system for signal quality visualization.
 *
 * Usage:
 *   const ps = new ParticleSystem(canvasElement, quality);
 *   ps.start();
 *   ps.setQuality(0.8);  // 0.0 (bad) to 1.0 (excellent)
 *   ps.destroy();
 */

class Particle {
  constructor(cx, cy, quality) {
    this.cx = cx;
    this.cy = cy;
    this.spawn(quality);
  }

  spawn(quality) {
    // Emit from a ring around the center
    const angle   = Math.random() * Math.PI * 2;
    const radius  = 8 + Math.random() * 24;
    this.x = this.cx + Math.cos(angle) * radius;
    this.y = this.cy + Math.sin(angle) * radius;

    // Speed: faster = better quality
    const speed  = 0.3 + quality * 2.2 + Math.random() * 0.8;
    this.vx = Math.cos(angle) * speed;
    this.vy = Math.sin(angle) * speed;

    // Life: longer = better quality
    this.maxLife = 40 + quality * 80 + Math.random() * 30;
    this.life    = this.maxLife;

    // Size: bigger = better
    this.size = 0.8 + quality * 2.2 * Math.random();

    // Hue: 0° red → 120° green (quality 0→1)
    this.hue = quality * 128;        // ~red to green
    this.sat = 80 + quality * 20;   // 80–100%
    this.lit = 45 + quality * 25;   // 45–70%

    // Small random "sparkle" twinkle
    this.twinkleOffset = Math.random() * Math.PI * 2;
    this.twinkleSpeed  = 0.08 + Math.random() * 0.12;
  }
}


class ParticleSystem {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {number} quality  0.0 (poor) to 1.0 (excellent)
   */
  constructor(canvas, quality = 0.5) {
    this.canvas  = canvas;
    this.ctx     = canvas.getContext('2d');
    this.quality = Math.max(0, Math.min(1, quality));
    this.particles = [];
    this.active    = false;
    this._raf      = null;
    this._tick     = 0;

    this.cx = canvas.width  / 2;
    this.cy = canvas.height / 2;
  }

  /** Update quality (0–1) and adjust system parameters. */
  setQuality(q) {
    this.quality = Math.max(0, Math.min(1, q));
  }

  // ── Private ──────────────────────────────────────────────────────────────

  get _maxParticles() {
    return Math.floor(6 + this.quality * 44);  // 6 (bad) → 50 (excellent)
  }

  get _spawnRate() {
    return 0.15 + this.quality * 0.55;          // probability per frame
  }

  _update() {
    this._tick++;

    // Spawn
    if (Math.random() < this._spawnRate && this.particles.length < this._maxParticles) {
      this.particles.push(new Particle(this.cx, this.cy, this.quality));
    }

    // Update existing
    this.particles = this.particles.filter(p => {
      p.x    += p.vx;
      p.y    += p.vy;
      p.vx   *= 0.97;   // gentle drag
      p.vy   *= 0.97;
      p.life--;
      return p.life > 0;
    });
  }

  _draw() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    for (const p of this.particles) {
      const lifeRatio = p.life / p.maxLife;

      // Twinkle: oscillate alpha
      const twinkle = 0.7 + 0.3 * Math.sin(this._tick * p.twinkleSpeed + p.twinkleOffset);
      const alpha   = lifeRatio * 0.85 * twinkle;

      const color   = `hsla(${p.hue}, ${p.sat}%, ${p.lit}%, ${alpha})`;
      const glow    = `hsla(${p.hue}, 100%, 70%, ${alpha * 0.5})`;

      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.shadowColor  = glow;
      ctx.shadowBlur   = 6;
      ctx.fill();
    }

    // Reset shadow so it doesn't bleed into other draws
    ctx.shadowBlur = 0;
  }

  _loop() {
    if (!this.active) return;
    this._update();
    this._draw();
    this._raf = requestAnimationFrame(() => this._loop());
  }

  // ── Public API ───────────────────────────────────────────────────────────

  start() {
    if (this.active) return;
    this.active = true;
    this._loop();
  }

  stop() {
    this.active = false;
    if (this._raf) {
      cancelAnimationFrame(this._raf);
      this._raf = null;
    }
  }

  destroy() {
    this.stop();
    this.particles = [];
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }
}
