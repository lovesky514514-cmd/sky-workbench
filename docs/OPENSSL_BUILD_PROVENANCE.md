# OpenSSL 3.5.9 build provenance (internal candidate)

The packaged Python `_ssl` and `_hashlib` extensions require the OpenSSL 3 ABI. Both shared libraries must be taken from the same verified build; they must never be mixed with the previous 3.5.8 DLLs.

- Upstream: official OpenSSL 3.5.9 source archive, SHA-256 `603F5602E2EEF00D77FBD429D34DCD5822BB301757A1BC9CDB24C670F1EB859A` (compared with the upstream `.sha256` file).
- Build: Windows x64, `VC-WIN64A`, Visual Studio 2022 v143 / MSVC 14.44.35207, NASM 2.16.01, Strawberry Perl 5.42.3.1 portable archive SHA-256 `6A081A811781C30ACA51DBC036AFD93092AF91E3297901F02C17043795A10690`.
- Upstream library tests: `nmake test` exit 0; 347 test files and 4,283 tests passed on the first build of this exact source and toolchain. A second build used a neutral mapped path and the default Program Files OpenSSL directories to avoid embedding the private workspace path. `nmake build_libs` exited 0. The second build did not rerun the full OpenSSL suite.
- Candidate `libcrypto-3-x64.dll`: SHA-256 `44A1C07CE88EB12A77FBCB38050B63A80BC61FF4EFB55758136D1A3C7AA8E2BB`.
- Candidate `libssl-3-x64.dll`: SHA-256 `EBF246E9A5FD0FA7B4B2DA9FCD838AF7B057AC133461DDED035BD18379426F23`.
- Python 3.12 compatibility smoke test: preloaded both candidate DLLs before importing `_ssl`; `ssl.OPENSSL_VERSION` reported 3.5.9, default TLS context exposed 17 ciphers, and `hashlib.sha256` returned the known digest. This does not replace frozen sidecar or network acceptance testing.
- Byte scan of the two candidate DLLs found zero references to the private workspace path and zero Windows user-directory paths. The neutral mapped `R:\` build path remains in debug/assertion metadata (7 and 3 occurrences respectively); it contains no personal identity. These are unsigned, locally built DLLs and must not be described as vendor-signed.

The library license text is the unchanged upstream Apache-2.0 text. The exact source archive, toolchain archive, build logs and resulting DLLs are retained outside the public source snapshot. This record establishes engineering provenance only; it is not legal approval for the full COMSOL-integrated application or a security exception sign-off for a future installer.

The first frozen Bridge probe exposed a separate Python packaging defect: this
builder's CPython 3.12.14 `ssl.py` had four local lines loading an internal PEM
file not present in the distributable. The official CPython 3.12.14 source
archive (SHA-256 `5C8462AF5790BAF43A321A1559DBE0DB06D1BE4300FB85FB53C40060668E548A`)
was fetched from python.org. Its unmodified `Lib/ssl.py` (SHA-256
`039A0E3F1782E14824B1624C7FFEFC2DE31714B4A9994424BE45A4271966097C`)
and original LICENSE are retained in `packaging/vendor/python-3.12.14/`.
PyInstaller replaces only the `ssl` pure module with that exact upstream file;
it retains normal TLS verification and the bundled certifi CA data. The
existing bundled CPython LICENSE contains the official 3.12.14 LICENSE text
as an exact prefix. The repaired frozen sidecar's native self-test constructed
an HTTPX TLS context successfully, and a separate COMSOL 6.3 Java Bridge
read-only model-tree probe returned verified readback while direct missing and
incorrect Bridge credentials were rejected with HTTP 401. This is a targeted
verification, not a claim that every COMSOL workflow passed.
