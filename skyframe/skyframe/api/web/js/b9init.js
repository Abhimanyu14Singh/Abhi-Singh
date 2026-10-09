/* SkyFrame — B9 / B11 wiring: frame auto mesh + output stations
   (js/framemesh.js), named point-spring properties + joint springs
   (js/springdlg.js), link hysteresis types (js/linkhyst.js).

   app.js calls initB9(window.__sky) once (after the ETABS chrome) and the
   Properties panel calls __sky.b9DecorateProps(box, {members, links, springs})
   after it renders. Menu items in js/etabs.js call the __sky.open* entry
   points lazily. */

import { initFrameMesh as b9iFrameMesh } from "./framemesh.js";
import { initSprings as b9iSprings } from "./springdlg.js";
import { initLinkHyst as b9iLinkHyst } from "./linkhyst.js";

export function initB9(sky) {
  const fm = b9iFrameMesh(sky);
  const sp = b9iSprings(sky);
  const lh = b9iLinkHyst(sky);
  sky.b9DecorateProps = (box, sel) => {
    for (const d of [fm, sp, lh]) {
      try { d.decorateProps(box, sel || {}); } catch (e) { console.error("b9 props decoration failed", e); }
    }
  };
  return { frameMesh: fm, springs: sp, links: lh };
}
