# Changelog

## [0.3.1](https://github.com/fecet/reconcile/compare/reconcile-v0.3.0...reconcile-v0.3.1) (2026-03-19)


### Bug Fixes

* skip extra-allow fields in validation ([fb12058](https://github.com/fecet/reconcile/commit/fb120586726025aa8b32e739f23f8f3754d62f76))

## [0.3.0](https://github.com/fecet/reconcile/compare/reconcile-v0.2.2...reconcile-v0.3.0) (2026-03-19)


### Features

* auto-discover explicitly-provided BaseModel fields as participants (hitchhike) ([b13d9e0](https://github.com/fecet/reconcile/commit/b13d9e0081553083a67fadcad8f2b6f3f5cd7a1a))
* resolve string annotations via pool namespace ([38ab3e3](https://github.com/fecet/reconcile/commit/38ab3e3b51f54b9fdb209e1d8efb39c4465dd0df))
* support multiple providers per field with fallback chain ([7060470](https://github.com/fecet/reconcile/commit/706047016a998546d099eedb57e54d95dce8e072))


### Bug Fixes

* multiple [@dependency](https://github.com/dependency) on same Field(default_factory=...) crashes ([45bd385](https://github.com/fecet/reconcile/commit/45bd385dd1fc7554a9d0e0f99cdfd0b5fa25ec79))


### Documentation

* add README explaining two-phase reconcile model ([fe280d2](https://github.com/fecet/reconcile/commit/fe280d2f73afd67772110e9f31e3d19b4de107d4))

## [0.2.2](https://github.com/fecet/reconcile/compare/reconcile-v0.2.1...reconcile-v0.2.2) (2026-03-09)


### Bug Fixes

* add x-release-please-version to package version ([731993d](https://github.com/fecet/reconcile/commit/731993d40d97c85cf2df3c9bbee3c1fbda265f02))
* support Field(default_factory=...) in [@dependency](https://github.com/dependency) ([fd08b65](https://github.com/fecet/reconcile/commit/fd08b656f4622311f15e7ab7198e154e435a2432))

## [0.2.1](https://github.com/fecet/reconcile/compare/reconcile-v0.2.0...reconcile-v0.2.1) (2026-03-02)


### Bug Fixes

* **ci:** disable --locked flag for lockfile update job ([5eb17bd](https://github.com/fecet/reconcile/commit/5eb17bdb9b0770eccdf111b155ec220e6d46e1fd))

## [0.2.0](https://github.com/fecet/reconcile/compare/reconcile-v0.1.0...reconcile-v0.2.0) (2026-03-01)


### Features

* enable pixi build for conda package output ([48f8608](https://github.com/fecet/reconcile/commit/48f8608cffe33667cba38b7b43d3015c5aaff3f0))
* support Field(default=X) as fallback for unresolved dependencies ([17428c8](https://github.com/fecet/reconcile/commit/17428c8c397f29c7773a87559eb4c7354cb806ba))
* support inheritance in dependency type resolution ([e0c6463](https://github.com/fecet/reconcile/commit/e0c6463ff24a3f28678ce9451bd03b722194b6cf))


### Bug Fixes

* use forward reference for Dependency in module-level annotation ([f7427a5](https://github.com/fecet/reconcile/commit/f7427a5d008bbdf7521893bd6304503c805b8b56))
