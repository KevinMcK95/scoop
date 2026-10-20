import { describe, expect, it } from 'vitest';
import { figureFileName, figuresOf } from './figures';
import type { ChatMessage } from '../workspace/contracts';

describe('figures from the conversation', () => {
  it('lists tool images newest first, with the question they answered', () => {
    const messages: ChatMessage[] = [
      { id: 1, role: 'user', text: 'Plot the CMD' },
      { id: 2, role: 'tool', text: 'run_python', status: 'success', detail: { code: 'plt.plot()', image: '/api/figures/t/a.png' } },
      { id: 3, role: 'tool', text: 'describe_field', status: 'success' },
      { id: 4, role: 'user', text: 'What is at the center?' },
      { id: 5, role: 'tool', text: 'sky_image', status: 'success', detail: { image: 'data:image/jpeg;base64,AAAA' } }
    ];
    const figures = figuresOf(messages);
    expect(figures.map((f) => [f.stepId, f.label, f.question])).toEqual([
      [5, 'Looked at the sky', 'What is at the center?'],
      [2, 'Ran Python', 'Plot the CMD']
    ]);
    expect(figures[1].code).toBe('plt.plot()');
    expect(figureFileName(figures[0], 2)).toBe('scoop_looked_at_the_sky_2.jpg');
    expect(figureFileName(figures[1], 1)).toBe('scoop_ran_python_1.png');
  });
});
