# Vendored browser libraries

Copied unmodified from the npm packages so the avatar works offline. Both are MIT
licensed; their LICENSE files sit next to them. Update by copying the same files
from a newer release and changing the versions below.

| Library | Version | Files | Source |
|---|---|---|---|
| three | 0.186.1 | `three/three.module.js`, `three/three.core.js`, `three/addons/loaders/GLTFLoader.js`, `three/addons/utils/BufferGeometryUtils.js` | npm `three@0.186.1` (`build/`, `examples/jsm/`) |
| @pixiv/three-vrm | 3.5.5 | `three-vrm/three-vrm.module.min.js` | npm `@pixiv/three-vrm@3.5.5` (`lib/`) |

The page maps them with an import map: `three` → `three/three.module.js`,
`three/addons/` → `three/addons/`, `@pixiv/three-vrm` → `three-vrm/three-vrm.module.min.js`.
