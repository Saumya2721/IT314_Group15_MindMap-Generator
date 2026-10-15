import React from 'react';
import GeneratingPage from './GeneratingPage';

/**
 * EditorPage delegates to GeneratingPage with initialPhase="editor".
 * This allows direct navigation from the Dashboard (e.g. clicking /editor/:id)
 * while keeping all editor and generation logic unified on the same page.
 */
export default function EditorPage() {
  return <GeneratingPage initialPhase="editor" />;
}
