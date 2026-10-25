// Convert MusicXML files to MuseScore .mscz with webmscore.
//
//   node to_mscz.cjs in1.musicxml out1.mscz [in2.musicxml out2.mscz ...]
//
// Prints one JSON line per pair: {"input", "output", "ok", "error"?}.
// Exits 0 even when single files fail; the caller reads the JSON lines.

// webmscore 1.2.1 predates Node >= 21: it assigns globalThis.navigator, which is now a
// read-only getter, and Emscripten tries to fetch() its .wasm by file path whenever a
// global fetch exists. Undo both before loading it.
Object.defineProperty(globalThis, 'navigator', { value: {}, writable: true, configurable: true })
delete globalThis.fetch

const fs = require('fs')
const WebMscore = require('webmscore')

async function main(args) {
  if (args.length === 0 || args.length % 2 !== 0) {
    console.error('usage: node to_mscz.cjs in.musicxml out.mscz [...]')
    process.exit(2)
  }
  await WebMscore.ready
  for (let i = 0; i < args.length; i += 2) {
    const [input, output] = [args[i], args[i + 1]]
    try {
      const score = await WebMscore.load('musicxml', new Uint8Array(fs.readFileSync(input)))
      fs.writeFileSync(output, await score.saveMsc('mscz'))
      // No score.destroy(): in 1.2.1 the next load() after destroy() crashes the module.
      // This process exits after one request, so the memory is released anyway.
      console.log(JSON.stringify({ input, output, ok: true }))
    } catch (error) {
      console.log(JSON.stringify({ input, output, ok: false, error: String(error) }))
    }
  }
}

main(process.argv.slice(2)).catch((error) => {
  console.error(String(error))
  process.exit(1)
})
