// Figures the agent's tools returned in this conversation (run_python plots, sky
// images), newest first, each with the question it answered.
import type { ChatMessage } from '../workspace/contracts';
import { stepLabel } from './activity';

export interface FigureEntry {
  stepId: number;
  image: string;
  label: string;
  question: string;
  code?: string;
}

export function figuresOf(messages: ChatMessage[]): FigureEntry[] {
  const out: FigureEntry[] = [];
  let question = '';
  for (const message of messages) {
    if (message.role === 'user') question = message.text;
    else if (message.role === 'tool' && message.detail?.image)
      out.push({ stepId: message.id, image: message.detail.image, label: stepLabel(message.text), question, code: message.detail.code });
  }
  return out.reverse();
}

/** A file name for downloading a figure, from its URL's type. */
export function figureFileName(figure: FigureEntry, index: number): string {
  const ext = /^data:image\/jpeg|\.jpg$/.test(figure.image) ? 'jpg' : 'png';
  return `scoop_${figure.label.toLowerCase().replace(/[^a-z0-9]+/g, '_')}_${index}.${ext}`;
}
