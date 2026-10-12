/* SkyFrame Wave B properties — menu registration (one init line in app.js).

     Define › Section Properties › Composite Column Sections…  [def-wb-composite]
     Define › Section Properties › Line / Area Spring Properties…  [def-wb-springprops]
     Assign › Shell › Line / Area Spring Property…  [asn-wb-springprops]

   Orthotropic materials (Material Property Data), slab / deck / CLT types
   (Slab / Wall Property Data) and cold-formed shapes (Frame Section
   Property Data) live inside the existing Define dialogs (js/wb_ortho.js,
   js/wb_slab.js, js/cfshapes.js). */

import { initComposite as wbiComposite } from "./wb_composite.js";
import { initWbSprings as wbiSprings } from "./wb_springs.js";

export function initWbProps(sky) {
  wbiComposite(sky);
  wbiSprings(sky);
}

export default { initWbProps };
