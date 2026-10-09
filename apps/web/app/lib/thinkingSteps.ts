// What a stored thinking step means, without its bookkeeping prefixes.
//
// The tool loop (apps/api/app/agents/tool_loop.py) saves each step as text with
// tags in front: "[ts:<ISO time>] " on every saved step (_ts_step), then
// "[reasoning] " for the model's own words between tool calls, or "[test] " for
// a write a test run only simulated. Live SSE steps arrive without the [ts:].
// Old rows keep whatever they were saved with, so the renderer strips the tags
// here rather than the API changing them.

export type ThinkingStepKind = 'reasoning' | 'test' | 'step';

export interface ThinkingStep {
  /** The [ts:…] value as stored (ISO 8601), when the step carried one. */
  time?: string;
  kind: ThinkingStepKind;
  text: string;
}

/** One leading tag: [ts:<anything but ]>], [reasoning] or [test]. */
const PREFIX_RE = /^\[(ts:[^\]]*|reasoning|test)\]\s*/;

/** Strip every leading tag, in any order, and say what they marked. Reasoning
 *  wins over test when both appear: it decides how the step is drawn. */
export function parseThinkingStep(raw: string): ThinkingStep {
  let text = raw ?? '';
  let time: string | undefined;
  let reasoning = false;
  let test = false;
  for (let m = text.match(PREFIX_RE); m; m = text.match(PREFIX_RE)) {
    const tag = m[1];
    if (tag === 'reasoning') reasoning = true;
    else if (tag === 'test') test = true;
    else if (time === undefined) time = tag.slice('ts:'.length).trim() || undefined;
    text = text.slice(m[0].length);
  }
  const kind: ThinkingStepKind = reasoning ? 'reasoning' : test ? 'test' : 'step';
  return time ? { time, kind, text } : { kind, text };
}
