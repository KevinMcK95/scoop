// Runs the agent's Python in Pyodide (CPython compiled to WebAssembly).
//
//   node runner.mjs --setup   download the scientific packages once (needs the network)
//   node runner.mjs           serve: one JSON request per stdin line, one JSON reply per stdout line
//
// scoop/sandbox.py starts this under OS restrictions (no network, no writes, no reads
// outside this directory and Node itself) and Node's permission model. The Python code
// gets the requested field tables as pandas DataFrames and nothing else.
import { constants as fsConstants } from 'node:fs';
import { createInterface } from 'node:readline';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadPyodide } from 'pyodide';

const here = dirname(fileURLToPath(import.meta.url));
const indexURL = join(here, 'node_modules', 'pyodide') + '/';
export const PACKAGES = ['numpy', 'scipy', 'pandas', 'matplotlib', 'astropy', 'scikit-learn', 'statsmodels',
  'sympy', 'uncertainties', 'shapely'];

// Python side: run code with the tables, capture output, the last expression and figures.
const HARNESS = `
import ast, base64, contextlib, io, json, traceback
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.show = lambda *args, **kwargs: None  # figures are collected after the run
import warnings
from astropy.utils import iers
from astropy.utils.data import conf as data_conf
# No network: use astropy's bundled Earth orientation and leap-second tables, quietly.
data_conf.allow_internet = False
iers.conf.auto_download = False
iers.conf.auto_max_age = None
warnings.filterwarnings("ignore", category=iers.IERSStaleWarning)

INSTALLED = "numpy, pandas, scipy, astropy, matplotlib, scikit-learn, statsmodels, sympy, uncertainties, shapely"

MAX_OUTPUT = 10_000
MAX_FIGURES = 2

def _scoop_run(code, tables_json):
    tables = {name: pd.DataFrame(t["rows"], columns=t["columns"]) for name, t in json.loads(tables_json).items()}
    namespace = {"__name__": "__main__", "np": np, "pd": pd, "plt": plt, "tables": tables}
    namespace.update({name: frame for name, frame in tables.items() if name.isidentifier()})
    out, error = io.StringIO(), None
    plt.close("all")
    try:
        tree = ast.parse(code, "<run_python>")
        last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            exec(compile(tree, "<run_python>", "exec"), namespace)
            if last is not None:
                value = eval(compile(ast.Expression(last.value), "<run_python>", "eval"), namespace)
                if value is not None:
                    print(repr(value))
    except BaseException:
        lines = traceback.format_exc().splitlines()
        # Keep the user's frames, not the harness's.
        start = next((i for i, l in enumerate(lines) if "<run_python>" in l), 1)
        error = "\\n".join([lines[0], *lines[start:]])[-4000:]
        if lines[-1].startswith("ModuleNotFoundError"):
            error += f"\\nThis sandbox cannot install packages. Available besides the standard library: {INSTALLED}."
    figures = []
    for number in plt.get_fignums()[:MAX_FIGURES]:
        buffer = io.BytesIO()
        plt.figure(number).savefig(buffer, format="png", dpi=100, bbox_inches="tight")
        figures.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
    count = len(plt.get_fignums())
    plt.close("all")
    text = out.getvalue()
    if len(text) > MAX_OUTPUT:
        text = text[:MAX_OUTPUT] + f"\\n[Output cut at {MAX_OUTPUT:,} of {len(text):,} characters.]"
    return json.dumps({"output": text, "error": error, "figures": figures, "figure_count": count})
`;

// Pyodide's file system reads Node's fs constants through process.binding, which the
// permission model blocks; answer that one call and nothing else.
const binding = process.binding;
process.binding = (name) => (name === 'constants' ? { fs: fsConstants } : binding.call(process, name));

async function start() {
  const pyodide = await loadPyodide({ indexURL, packageCacheDir: indexURL, stdout: () => {}, stderr: () => {} });
  await pyodide.loadPackage(PACKAGES, { messageCallback: () => {}, errorCallback: () => {} });
  return pyodide;
}

if (process.argv.includes('--setup')) {
  const pyodide = await start();
  await pyodide.runPythonAsync('import numpy, scipy, pandas, matplotlib, astropy, sklearn');
  console.log(`Pyodide ${pyodide.version} with ${PACKAGES.join(', ')} is ready in ${indexURL}`);
  process.exit(0);
}

const pyodide = await start();
pyodide.runPython(HARNESS);
// No way out from the Python side beyond the OS sandbox: drop the JavaScript network APIs too.
for (const name of ['fetch', 'WebSocket', 'XMLHttpRequest', 'EventSource']) delete globalThis[name];
const run = pyodide.globals.get('_scoop_run');
const reply = (message) => process.stdout.write(JSON.stringify(message) + '\n');
reply({ ready: true, version: pyodide.version });

for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
  let request;
  try {
    request = JSON.parse(line);
    const result = JSON.parse(run(String(request.code), JSON.stringify(request.tables ?? {})));
    reply({ id: request.id, ...result });
  } catch (error) {
    reply({ id: request?.id ?? null, output: '', error: `Sandbox error: ${error?.message ?? error}`, figures: [], figure_count: 0 });
  }
}
