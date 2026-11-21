import React from 'react';
import {Composition, registerRoot} from 'remotion';
import {Overlay} from './Overlay';

const sample = {
  fps: 30, duration: 8, peak: 4, accent: '#FFD400', hookText: '#111111', font: 'Impact',
  hook: 'Sample hook goes here', hookY: 270, captionY: 1400, marks: 'Your Channel', marksY: 1544,
  chunks: [], beats: [], avatar: {enabled: false},
};

const Root = () => (
  <Composition
    id="Overlay"
    component={Overlay}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={240}
    defaultProps={sample}
    calculateMetadata={({props}) => ({fps: props.fps, durationInFrames: Math.max(1, Math.round(props.duration * props.fps))})}
  />
);

registerRoot(Root);
