// The animated text layer, rendered transparent and laid over the footage by ffmpeg.
// Everything is driven by the props that clipper/motion.py writes: hook, caption chunks, text beats, brand colours.
import React from 'react';
import {AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'};
const outline = (px) => ({
  WebkitTextStroke: `${px}px #000`,
  paintOrder: 'stroke fill',
  textShadow: '0 6px 0 rgba(0,0,0,0.55)',
});

// Hook: punches in during the first third of a second, then sits still so it stays readable.
const Hook = ({p}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  if (!p.hook) return null;
  const s = spring({frame, fps, config: {damping: 11, stiffness: 170, mass: 0.7}});
  const size = p.hook.length > 36 ? 58 : p.hook.length > 24 ? 68 : 78;
  return (
    <div style={{position: 'absolute', top: p.hookY, left: 0, width: 1080, display: 'flex', justifyContent: 'center',
      transform: `translateY(-50%) scale(${interpolate(s, [0, 1], [0.5, 1])}) rotate(${interpolate(s, [0, 1], [-5, 0])}deg)`}}>
      <div style={{background: p.accent, color: p.hookText, fontFamily: p.font, fontSize: size, lineHeight: 1.16,
        padding: '22px 38px', borderRadius: 26, maxWidth: 940, textAlign: 'center', textTransform: 'uppercase',
        boxShadow: '8px 10px 0 rgba(0,0,0,0.6)'}}>
        {p.hook}
      </div>
    </div>
  );
};

// Captions: the group slides up as it appears, and each word pops and turns accent-coloured as it is spoken.
const Captions = ({p}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  const chunk = p.chunks.find((c) => t >= c.start && t < c.end);
  if (!chunk) return null;
  const enter = spring({frame: frame - chunk.start * fps, fps, config: {damping: 14, stiffness: 260, mass: 0.5}});
  const chars = chunk.words.reduce((n, w) => n + w.w.length + 1, 0);
  const size = chars > 18 ? 74 : chars > 13 ? 86 : 98;
  let active = 0;
  chunk.words.forEach((w, i) => { if (t >= w.s) active = i; });
  return (
    <>
    {chunk.emoji ? (
      <div style={{position: 'absolute', top: p.captionY - 205, left: 0, width: 1080, textAlign: 'center', fontSize: 120,
        transform: `scale(${interpolate(enter, [0, 1], [0.2, 1])}) rotate(${interpolate(enter, [0, 1], [-25, 0])}deg)`}}>{chunk.emoji}</div>
    ) : null}
    <div style={{position: 'absolute', top: p.captionY, left: 0, width: 1080, display: 'flex', justifyContent: 'center',
      gap: size * 0.36, transform: `translateY(calc(-50% + ${interpolate(enter, [0, 1], [34, 0])}px))`,
      opacity: interpolate(enter, [0, 0.4], [0, 1], clamp)}}>
      {chunk.words.map((w, i) => {
        const pop = spring({frame: frame - w.s * fps, fps, config: {damping: 8, stiffness: 300, mass: 0.45}});
        const on = i === active;
        return (
          <span key={i} style={{fontFamily: p.font, fontSize: size, color: on ? p.accent : '#fff', display: 'inline-block',
            transform: `scale(${on ? interpolate(pop, [0, 1], [1.4, 1.1]) : 1})`, ...outline(Math.round(size / 9))}}>
            {w.w}
          </span>
        );
      })}
    </div>
    </>
  );
};

// Beats: our own on-screen commentary. "setup" and "watch" are pills at the top of the gameplay,
// "payoff" is the big reaction that slams in after the peak.
const Beat = ({b, p}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const local = frame - b.start * fps;
  const total = (b.end - b.start) * fps;
  if (local < 0 || local >= total) return null;
  const s = spring({frame: local, fps, config: {damping: 10, stiffness: 220, mass: 0.6}});
  const out = interpolate(local, [total - 5, total], [1, 0], clamp);
  if (b.kind === 'payoff') {
    const shake = local < 10 ? Math.sin(local * 2.6) * (10 - local) * 0.9 : 0;
    return (
      <div style={{position: 'absolute', top: p.captionY - 520, left: 0, width: 1080, display: 'flex', justifyContent: 'center',
        opacity: out, transform: `translateX(${shake}px) scale(${interpolate(s, [0, 1], [2.1, 1])}) rotate(-3deg)`}}>
        <span style={{fontFamily: p.font, fontSize: 118, color: p.accent, textTransform: 'uppercase', textAlign: 'center',
          maxWidth: 1000, lineHeight: 1.05, ...outline(14)}}>{b.text}</span>
      </div>
    );
  }
  return (
    <div style={{position: 'absolute', top: p.beatY, left: 0, width: 1080, display: 'flex', justifyContent: 'center',
      opacity: out, transform: `translateY(${interpolate(s, [0, 1], [-40, 0])}px) scale(${interpolate(s, [0, 1], [0.8, 1])})`}}>
      <span style={{fontFamily: p.font, fontSize: 56, color: '#fff', background: 'rgba(0,0,0,0.82)', padding: '14px 30px',
        borderRadius: 18, border: `5px solid ${p.accent}`, textTransform: 'uppercase', maxWidth: 960, textAlign: 'center'}}>
        {b.text}
      </span>
    </div>
  );
};

// A short white flash exactly on the peak: makes the moment land even with the sound off.
const Flash = ({p}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const o = interpolate(frame - p.peak * fps, [0, 2, 9], [0, 0.32, 0], clamp);
  return o > 0 ? <AbsoluteFill style={{background: '#fff', opacity: o}} /> : null;
};

// Avatar: the channel's own face in a webcam-style tile. It talks while the narrator speaks, leans in on the
// "watch" cue, is shocked on the peak and laughs through the payoff. Five still images, swapped and animated.
const inside = (t, r) => r && t >= r[0] && t < r[1];
const POSES = ['idle', 'talk', 'watch', 'shock', 'laugh'];

const Avatar = ({p}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const a = p.avatar;
  if (!a || !a.enabled) return null;
  const t = frame / fps;
  let pose = 'idle';
  if (a.talk.some((r) => inside(t, r))) pose = 'talk';
  else if (inside(t, a.shock)) pose = 'shock';
  else if (inside(t, a.laugh) && !a.voice.some((r) => inside(t, r))) pose = 'laugh';
  else if (inside(t, a.watch)) pose = 'watch';
  const enter = spring({frame: frame - 4, fps, config: {damping: 12, stiffness: 160, mass: 0.7}});
  const jolt = a.shock ? spring({frame: frame - a.shock[0] * fps, fps, config: {damping: 6, stiffness: 320, mass: 0.5}}) : 0;
  const breathe = Math.sin(frame / 22) * 0.008;
  let scale = 1 + breathe, dy = 0, rot = Math.sin(frame / 37) * 0.5;
  if (pose === 'watch') scale = 1.07;
  if (pose === 'shock') { scale = interpolate(jolt, [0, 1], [1.28, 1.1]); dy = -interpolate(jolt, [0, 1], [26, 6]); }
  if (pose === 'laugh') { dy = Math.sin(frame * 1.25) * 7; rot = Math.sin(frame * 0.62) * 2.2; scale = 1.05; }
  if (pose === 'talk') dy = -3;
  return (
    <div style={{position: 'absolute', left: a.x, top: a.y, width: a.size, height: a.size, borderRadius: 30, overflow: 'hidden',
      border: `7px solid ${p.accent}`, boxShadow: '0 12px 30px rgba(0,0,0,0.65)', background: '#111',
      transform: `translateY(${interpolate(enter, [0, 1], [320, 0])}px)`}}>
      {POSES.map((name) => (
        <Img key={name} src={staticFile(`avatar/${name}.png`)} style={{position: 'absolute', inset: 0, width: '100%', height: '100%',
          objectFit: 'cover', opacity: name === pose ? 1 : 0, transform: `translateY(${dy}px) scale(${scale}) rotate(${rot}deg)`}} />
      ))}
    </div>
  );
};

export const Overlay = (p) => (
  <AbsoluteFill>
    <Flash p={p} />
    <Hook p={p} />
    {p.beats.map((b, i) => <Beat key={i} b={b} p={p} />)}
    <Captions p={p} />
    <Avatar p={p} />
    {p.marks ? (
      <div style={{position: 'absolute', top: p.marksY, left: 0, width: 1080, textAlign: 'center', transform: 'translateY(-50%)',
        fontFamily: p.font, fontSize: 34, color: 'rgba(255,255,255,0.75)', ...outline(5)}}>{p.marks}</div>
    ) : null}
  </AbsoluteFill>
);
