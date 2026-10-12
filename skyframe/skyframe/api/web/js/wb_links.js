/* SkyFrame — wave-B links & damping wiring (one import + one init line in
   js/app.js):

     js/linkext.js   Plastic (Wen) · BRB · bilinear damper · T/C isolator link
                     types (Assign › Link › Link Properties… dialog)
     js/linkfreq.js  Define › Section Properties › Frequency-Dependent Link
                     Properties…
     js/dampopts.js  Analyze › Advanced › Damping Options… (Rayleigh on links +
                     material damping ratios) and the composite-damping fields
                     of the TH / modal case dialogs
     js/mock_wbdamp.js  ?mock=1 validation, composite preview, results,
                     DB-edit tables

   Additive: __sky.wb = { linkExt, linkFreq, damp }. */

import { initLinkExt as wbInitLinkExt } from "./linkext.js";
import { initLinkFreq as wbInitLinkFreq } from "./linkfreq.js";
import { initDampOpts as wbInitDampOpts } from "./dampopts.js";
import { wbMockAugment as wbAugment, wbMockValidate as wbValidate } from "./mock_wbdamp.js";

export { wbValidate as wbMockValidateModel };

export function initWaveBLinks(sky) {
  if (!sky || !sky.store) return null;
  const lx = wbInitLinkExt(sky);
  const lf = wbInitLinkFreq(sky);
  const dp = wbInitDampOpts(sky);
  if (sky.menu && sky.menu.add) {
    try {
      sky.menu.add("Define/Section Properties", { label: "Frequency-Dependent Link Properties…", act: "def-wb-linkfreq", before: "def-pt-tendons",
        hint: "K(f) · C(f) for Steady State / PSD", fn: () => sky.openLinkFreqProps() });
    } catch (e) { console.warn("menu add def-wb-linkfreq", e); }
    try {
      sky.menu.add("Analyze/Advanced", { label: "Damping Options…", act: "an-wb-damping", before: "an-acc-advisor",
        hint: "Rayleigh on links · material (composite) ratios", fn: () => sky.openDampingOptions() });
    } catch (e) { console.warn("menu add an-wb-damping", e); }
  }
  document.addEventListener("sky:results-changed", () => {
    const S = sky.store;
    if (S.mock && S.results && S.model) wbAugment(S.model, S.results);
  });
  sky.wb = Object.assign(sky.wb || {}, { linkExt: lx, linkFreq: lf, damp: dp });   // D2 fix: keep wb.views / wb.windows / wb.draw3d (wbi_init.js)
  return sky.wb;
}
