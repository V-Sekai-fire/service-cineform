# service-cineform

The CineForm encode service: it stands up the iceoryx2 bus and the encoder on one host, in a runtime directory the service owns.

## What it is for

The encoder and the terminal display are two processes that must agree on a shared-memory namespace, a library path and a runtime directory. Those belong to the deployment rather than to either program, so this repository owns them, with the round-trip test that runs against the live bus. RFD 1137 owns the design.

## Build and run

Build `interactor-cineform` and `transport-cineform-tui` first, then:

```sh
pixi run -e build python scripts/up.py
```

`default.xml` lists what the service composes, for a checkout with no workspace around it.

## Licence

Apache-2.0 OR MIT; see `LICENSE-APACHE` and `LICENSE-MIT`.
