# Vendored browser libraries

Copied unmodified from the npm packages so the avatar works offline. Both are MIT
licensed; their LICENSE files sit next to them. Update by copying the same files
from a newer release and changing the versions below.

| Library | Version | Files | Source |
|---|---|---|---|
| three | 0.186.1 | `three/three.module.js`, `three/three.core.js`, `three/addons/loaders/GLTFLoader.js`, `three/addons/utils/BufferGeometryUtils.js`, `three/addons/utils/SkeletonUtils.js` | npm `three@0.186.1` (`build/`, `examples/jsm/`) |
| @pixiv/three-vrm | 3.5.5 | `three-vrm/three-vrm.module.min.js` | npm `@pixiv/three-vrm@3.5.5` (`lib/`) |
| pixi.js | 8.21.0 | `pixi/pixi.min.mjs` (self-contained ESM) | npm `pixi.js@8.21.0` (`dist/`) |
| untitled-pixi-live2d-engine | 1.4.0 | `live2d-engine/cubism.es.js` (Cubism 3/4/5 entry; imports only `pixi.js`) | npm `untitled-pixi-live2d-engine@1.4.0` (`dist/`) |

The page maps them with an import map: `three` → `three/three.module.js`,
`three/addons/` → `three/addons/`, `@pixiv/three-vrm` → `three-vrm/three-vrm.module.min.js`.
GLTFLoader imports both addon utils, so each must be copied with it: one missing file
stops the whole page (`tests/test_avatar_web.py` walks the imports to catch that).

The Live2D engine (MIT) bundles Live2D's Cubism Web Framework, which Live2D publishes under
the Live2D Open Software License. It needs Live2D's proprietary **Cubism Core**
(`live2dcubismcore.min.js`), which is NOT vendored here: the owner downloads the official
Cubism SDK for Web (accepting Live2D's licences at download), and Cyrene serves the file from
`avatar.live2d_core` unmodified. The Live2D page maps `pixi.js` → `pixi/pixi.min.mjs` and
`untitled-pixi-live2d-engine/cubism` → `live2d-engine/cubism.es.js`.
