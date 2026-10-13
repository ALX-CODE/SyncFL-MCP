# Native save result contract

`fl_project_save_native` saves the current named project through the typed
controller command `project.save_current`. It does not activate a window or send
keyboard or mouse input. The exact inspected window title, controller process,
project revision and stopped transport must remain valid.

The command's return and the observed project state are separate evidence:

- `transport_return` preserves the raw `transport.globalTransport` return;
  `response.handled` retains the same value for compatibility.
- `dispatch_ack` names a recognized routing code, such as `recognized_global`
  for 8, or reports `unrecognized`. A normal function return means dispatch
  completed without an exception. An unfamiliar integer is not an explicit
  failure and is not treated as a successful acknowledgement.
- `saved_current_host` requires the host's changed flag to be 1 or 2 before the
  actual command and two consecutive later independent callbacks to report 0.
  Identity, revision and stopped transport are checked again during readback.
- `already_clean` is a successful no-op and never claims a save was performed.
  A clean project does not receive the save command. Apply still creates its
  requested new receipt. If the project becomes clean immediately before the
  controller would dispatch, two independent callbacks confirm that no-op.
- A function exception, missing response or failed verification cannot become
  successful merely because later state is clean. Uncertain saves are never
  retried automatically.

Every result keeps `absolute_path_unknown: true` and `disk_verified: false`.
Host state is not proof of the destination path or persisted file bytes.

The [official MIDI scripting reference](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/midi_scripting.htm)
documents an integer return for `globalTransport`, routing constants including
`GT_Global` and return-only `GT_Delayed`, and changed-flag values 0, 1 and 2.
It does not promise that `FPT_Save` must return 8. The native result contract
therefore derives its save-state claim from explicit changed-flag observations,
without reinterpreting an unfamiliar return value.

On October 12, 2026, three consecutive saves in the disposable FL 26.1.4 lab
passed this contract (140, 141, then 140 BPM). Each exact known lab file was
independently parsed after saving, with its expected tempo and a new file hash.
The foreground window was unchanged across the sequence. This is evidence for
that lab and build; it does not give the general tool path discovery or disk
verification. The capability advertises `host_state_only`.
