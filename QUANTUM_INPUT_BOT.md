# Quantum input bot

`python app.py` hosts the quantum-input webhook bot alongside the existing chemistry drawing bot. The quantum bot generates text files; it does not install or run ORCA, Psi4, or Gaussian. The drawing bot's polling handlers and rendering modules are unchanged.

## Telegram workflow

1. Send `#benzene`, upload a `.xyz`, `.mol`, or `.sdf` coordinate file, or send `/xyz` followed by Cartesian coordinates. A bare XYZ block (`Element x y z`) and an XYZ atom-count header are supported. Use a **3D geometry** appropriate for your calculation.
2. Choose ORCA 6.1 (`.inp`), Psi4 (`.dat`), or Gaussian 16 for Linux (`.gjf`). Guided choices request charge, spin multiplicity, methods, basis sets, solvent settings, and resources. Gaussian includes single point, optimization, frequencies, optimization plus frequencies, TS plus frequencies, TD-DFT, and NMR presets. Psi4 retains its advanced SCF/grid/convergence and wavefunction-output controls.
3. For expert input, send `/manual` and choose Gaussian or Psi4. Paste the complete input in one Telegram message or upload `.gjf`/`.com` (including GaussView exports) or `.dat`. Long inputs must be uploaded as files; the limit is 128 kB. The bot preserves Gaussian calculation settings and line order, and automatically converts Windows checkpoint/scratch paths in recognized Link 0 directives (for example `%Chk=C:\\folder\\job.chk` → `%Chk=job.chk`), including multi-step `--Link1--` jobs. It accepts UTF-8, UTF-16 with BOM, and Windows-1252 Gaussian text. The bot reports external dependencies such as `%OldChk` and `@basis.gbs` that must be transferred independently. It does not modify the Gaussian route, molecular coordinates, method, basis, memory, core count, or MaxDisk setting. Existing manual MaxDisk validation (up to 20000 MB) remains in force. Unknown embedded Windows paths are left intact with a warning; you must fix them explicitly before running on Linux. It does not execute user-supplied Psithon. Check checkpoint and scratch paths when moving a GaussView file from Windows to Linux.
4. `/help` repeats the start message; `/cancel` clears the current wizard. Separate senders in a group have separate sessions. Sessions exist only in RAM, expire after 30 minutes of inactivity, and are deleted after an input file is delivered successfully. A periodic cleanup job purges idle session data. No database or persistent job/output storage is used. Render Free may suspend idle services; uninterrupted uptime requires an always-on hosting plan.

The guided Gaussian file uses `%NProcShared`, **total** `%Mem`, a relative `%Chk`, and the route keyword `MaxDisk` (100–20000 MB). `MaxDisk` is a Gaussian algorithm/storage hint, **not** a guarantee that all temporary files or the total job remain under that size. A manually supplied Gaussian input is checked for explicit `MaxDisk` values exceeding 20000 MB. Presets do not invent unsupported Gaussian D4 keywords; a Gaussian D3(BJ) preset is available for B3LYP/PBE1PBE. Gaussian PBE0 is written as `PBE1PBE`. Some expert combinations need additional basis/ECP or checkpoint sections: use the manual path.

Psi4 PCM requires a Psi4 installation with PCMSolver support. Psi4 D4 method aliases require its optional `dftd4` module. Guided spin parity checks catch impossible electron counts, but cannot determine the correct chemical spin state, ground state, basis availability, or convergence. Verify each generated input against your installed engine and inspect the final output.

## Deployment and checks

The existing deployment uses `ORCA_BOT_TOKEN` for the quantum bot and `CHEM_BOT_TOKEN` for the independent drawing bot. Existing Telegram webhooks continue to receive messages if `ORCA_WEBHOOK_SECRET` has not yet been set. Set `ORCA_WEBHOOK_SECRET` to protect new registrations and the webhook; `/registerWebhook` and `/deleteWebhook` require it. After adding or changing the secret, register the webhook again so Telegram sends the matching secret header. Keep the token and secret in environment variables; neither belongs in Git. Retain the existing `python app.py` startup for the combined two-bot process. No additional runtime dependency was introduced.

Run local verification with Python 3.12:

```bash
python -m pip install -r requirements.txt pytest
python -m pytest -q
```

The test suite uses mocked Telegram messages and does not require ORCA, Psi4, a Gaussian license, or bot tokens. It checks generated text and wizard transitions, not an engine's execution. After deployment, test `/start`, one `.xyz` upload, a Gaussian file, a Psi4 file, and a GaussView upload through Telegram before relying on the bot for calculations.
