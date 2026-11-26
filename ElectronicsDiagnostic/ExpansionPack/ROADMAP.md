# Roadmap

Prioritized list of future work. Items are ordered by impact.

## Near-term

1. **Expand the component database.** Add more verified records per
   category, prioritizing parts commonly seen on laptop/desktop boards
   the host application services. Each record must carry source
   attribution; no fabrication.
2. **Expand the rail-debugging rule set.** Add explicit branches for
   undervoltage, overvoltage, unstable output, and intermittent faults
   (currently these surface as "no rule fired" with the prior step's
   interpretation guidance).
3. **More repair-case seeds.** Add 10-20 verified repair cases across
   common laptop/desktop fault patterns to give the FailurePatternDB
   real search depth.

## Medium-term

4. **Table extraction plug-in.** Define a host-injected extractor
   interface so the host can plug in pdfplumber/camelot for datasheet
   table extraction without breaking the pack's pure-stdlib constraint.
5. **Topological short-hunt guidance.** Extend the sequence graph to
   suggest which downstream rails to isolate first when an upstream
   rail is shorted.
6. **Multi-language report rendering.** The framework currently emits
   English Markdown; add a locale map for the labels.

## Long-term

7. **Image-based topology recognition.** Only after structured-input
   recognition is proven reliable. Requires a vision model; would be
   host-injected, not bundled.
8. **Automatic rule promotion from approved repair cases.** Currently
   promotion stops at "reusable knowledge"; a future step could draft
   diagnostic rules from recurring patterns, with mandatory human
   review before activation.
9. **Federated case sharing.** Allow technicians to share anonymized
   repair cases across sites without exposing board-identity data.

## Explicitly out of scope

- BIOS/firmware/schematic/BoardView auto-ingestion (spec forbids).
- Bundling copyrighted books (spec forbids).
- Replacing the host's existing CLI/sessions/reports (spec requires
  preservation).
