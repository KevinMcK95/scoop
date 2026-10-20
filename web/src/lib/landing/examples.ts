// Questions offered on the start page. Each works from an empty workspace: the
// agent loads a field that has been run, or queries the archives for a new one.
export interface Example {
  icon: string;
  label: string; // what the chip says
  prompt: string; // what is sent
}

export const EXAMPLES: Example[] = [
  { icon: '✦', label: 'Likely member stars of the Draco dwarf galaxy',
    prompt: 'Show me the Draco dwarf galaxy and its likely member stars from Gaia proper motions.' },
  { icon: '◎', label: 'Quasars behind M31 with HST or JWST imaging',
    prompt: 'Which trusted quasars behind M31 fall inside HST or JWST images, and with what time baselines?' },
  { icon: '▦', label: "Leo II's colour–magnitude diagram",
    prompt: 'Plot the colour–magnitude diagram of Leo II and highlight stars with spectra.' },
  { icon: '⟳', label: "What's around NGC 147? (queries the archives)",
    prompt: 'Run a 10 arcmin cone around NGC 147 and tell me what is there: imaging, Gaia stars and spectra.' },
  { icon: '📚', label: 'Recent papers on dark matter in dwarf spheroidals',
    prompt: 'Find recent papers on the dark matter content of Milky Way dwarf spheroidals and summarize the main results.' }
];
