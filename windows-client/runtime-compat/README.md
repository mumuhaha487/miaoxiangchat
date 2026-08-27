# Pinned Visual C++ runtime

These Microsoft Visual C++ Redistributable DLLs are version `14.44.35211.0`.
They are the runtime set validated with the client's pinned ONNX Runtime 1.22.1
build on Windows 10 and Windows 11.

`MiaoxiangComputerAgent.spec` replaces build-host copies of the same filenames
with these files. Do not update them without running the packaged `--self-test`
on every supported Windows version.
