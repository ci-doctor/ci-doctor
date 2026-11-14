"""Where matchers come from: registries, how they are fetched and cached, and building one.

`resolve` turns `matchers.include` into matcher files, through `transports/` (a folder
or an `https://` URL) and the local `cache`. `index` builds a registry's `index.yaml`
and bundles; `mirror` copies a whole registry into a folder. What a matcher file is,
and how one is tested, is `ci_doctor.matchers`: imports run `registry` → `matchers`.
"""
