// Convert scores with webmscore (MuseScore 4 compiled to WebAssembly).
//
//   node convert.cjs jobs.json
//
// jobs.json is a list of {"input": path, "format": "musicxml" | "mxl" | "mscz" | "mscx",
//                          "outputs": {"mscz"?: path, "musicxml"?: path, "midi"?: path, "pdf"?: path}}
// Prints one JSON line per job: {"input", "written": [kinds], "errors": {kind: message}}.
// Exits 0 even when single jobs fail; the caller reads the JSON lines.

// webmscore 1.2.1 predates Node >= 21: it assigns globalThis.navigator, which is now a
// read-only getter, and Emscripten tries to fetch() its .wasm by file path whenever a
// global fetch exists. Undo both before loading it.
Object.defineProperty(globalThis, 'navigator', { value: {}, writable: true, configurable: true })
delete globalThis.fetch

const fs = require('fs')
const WebMscore = require('webmscore')

const WRITERS = {
  mscz: (score) => score.saveMsc('mscz'),
  musicxml: (score) => score.saveXml(),
  midi: (score) => score.saveMidi(true, true), // expand repeats, include expressions
  pdf: (score) => score.savePdf(),
}

async function main([jobsPath]) {
  if (!jobsPath) {
    console.error('usage: node convert.cjs jobs.json')
    process.exit(2)
  }
  const jobs = JSON.parse(fs.readFileSync(jobsPath, 'utf8'))
  await WebMscore.ready
  for (const job of jobs) {
    const report = { input: job.input, written: [], errors: {} }
    let score
    try {
      score = await WebMscore.load(job.format, new Uint8Array(fs.readFileSync(job.input)))
    } catch (error) {
      report.errors.load = String(error)
      console.log(JSON.stringify(report))
      continue
    }
    for (const [kind, path] of Object.entries(job.outputs || {})) {
      try {
        fs.writeFileSync(path, await WRITERS[kind](score))
        report.written.push(kind)
      } catch (error) {
        report.errors[kind] = String(error)
      }
    }
    // No score.destroy(): in 1.2.1 the next load() after destroy() crashes the module.
    // This process exits after one request, so the memory is released anyway.
    console.log(JSON.stringify(report))
  }
}

main(process.argv.slice(2)).catch((error) => {
  console.error(String(error))
  process.exit(1)
})
