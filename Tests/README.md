As part of rdkservices open source activity and logical grouping of services into various entservices-* repos, the below listed change to L1 and L2 Test are effective hence forth.

# Changes Done:
Since the mock part is common across various plugins/repos and common for L1, L2 & etc, the gtest and gmock related stubs (including platform interface mocks) are moved to a new repo called "entservices-testframework" and L1 & L2 test files of each plugin moved to corresponding repos, you can find them inside Tests directory of each entservices-*.
Hence, any modifications/additions related to mocks should be commited to entservices-testframework repo @ rdkcentral and any modifications/additions related to test case should be commited to Test directory of corresponding entservices repo.

# Individual Repo Handling
Each individual entservices-* repo was added with a .yml file to trigger L1, L2, L2-OOP test job in github workflow. This yml file triggers below mentioned build jobs in addition to regular build jobs (thunder, thunder tools & etc,).
```
a/ Build mocks => To create TestMock Lib from all required mock relates stubs and copy to install/usr/lib path.
b/ Build entservices-<repo-name> => To create Test Lib of .so type from all applicable test files which are enabled for plugin test.
c/ Build entservices-testframework => To create L1/L2  executable by linking the plugins/test .so files.
```
This ensures everything in-tact in repo level across multiple related plugins when there is a new change comes in.

##### Steps to run L1, L2, L2-OOP test locally #####
```
1. checkout the entservices-<repo-name> to your working directory in your build machine.
example: git clone https://github.com/rdkcentral/entservices-deviceanddisplay.git

2. switch to entservices-<repo-name> directory
example: cd entservices-deviceanddisplay

3. check and ensure current working branch points to develop
example: git branch

4. Run below curl command to download act executable to your repo.
example: curl -SL https://raw.githubusercontent.com/nektos/act/master/install.sh | bash

5. Run L1, L2, L2-oop test
example: ./bin/act -W .github/workflows/tests-trigger.yml -s GITHUB_TOKEN=<your access token>

NOTE: By default test-trigger.yml will trigger all tests(L1, L2 and etc) parallely, if you want any one test alone to be triggered/verified then remove the other trigger rules from the tests-trigger.yml
```
# testframework Repo Handling
tf-trigger.yml file of testframework repo will get loaded into github action whenever there is a pull or push happens. This file in-turn triggers all individual repos L1, L2, L2-oop tests. testframework repo test can run only in github workflow.

NOTE:
If you face any secret token related error while run your yml, pls comment the below mentioned line
#token: ${{ secrets.RDKE_GITHUB_TOKEN }}

# Execution usecases where manual change required before triggering the test:
```
a/ changes in testframework repo only:
Need to change ref pointer of "Checkout entservices-testframework" job in individual repo yml file, to point your current working branch of testframework and in tftrigger.yml of testframework repo need to change trigger branch name to your individual repo branch name instead of develop which is default.
example:
ref: topic/method_1  /* Checkout entservices-testframework job */
uses: rdkcentral/entservices-deviceanddisplay/.github/workflows/L1-tests.yml@topic/method_1 /* tf-trigger.yml */

b/ changes in both testframework repo and invidual repo:
Changes mentioned in step (a) above + "Checkout entservices-deviceanddisplay-testframework" job in individual repo yml file, ref field to point your deviceanddisplay current working branch.
example:
ref: topic/method_1 /* Checkout entservices-testframework job */
ref: topic/method_1 /* Checkout entservices-deviceanddisplay-testframework job */
uses: rdkcentral/entservices-deviceanddisplay/.github/workflows/L1-tests.yml@topic/method_1 /* tf-trigger.yml */

c/ changes in individual entservices-* repo only
no changes required
```

# Notes for anyone extending the L2 suite

Three things about this suite are not obvious from the code and each of them cost a full
20-minute run to discover. They are recorded here so the next person does not pay for them again.

## 1. The whole L2 suite has a hard 15-minute wall-clock ceiling

`entservices-testframework/Tests/L2Tests/L2testController.cpp` invokes the entire
`RUN_ALL_TESTS()` through a single COM-RPC call, and that call carries Thunder's
`RPC::CommunicationTimeOut`, which the framework's own
`patches/Increase_Timout_For_L2Tests_Plugin.patch` sets to 900000 ms. When the suite outlasts
it the controller logs `L2 tests failed: -2147483637` (`error | ERROR_TIMEDOUT`) and
**stops Thunder while gtest is still running**. Every remaining test's
`Controller.1.activate`/`deactivate` then returns `ERROR_TIMEDOUT` (11) and the tail of the
suite fails as collateral — including tests that are perfectly healthy. The wrapper still
exits 0 in that state and writes no results file.

The measured baseline for this plugin was **117 tests in 852.84 s**, i.e. 47 s of headroom,
with roughly 6.5 s of that per test spent activating and deactivating PowerManager and
HdmiCecSink (the fixture destructor alone contains `sleep(5)`) and about 20 s of run-to-run
variance. In other words the suite was already within a few percent of failing spontaneously.

`Tests/run_coverage.sh` therefore runs L2 in **two GoogleTest shards** by default
(`GTEST_TOTAL_SHARDS` / `GTEST_SHARD_INDEX`, so nothing is coupled to test names), each a
fresh process with a fresh 15-minute budget. gcov merges every shard's counters into the same
`.gcda` files on process exit, so the capture sees the union with no lcov merge involved.
Set `L2_SHARDS=1` to reproduce the single-process behaviour, and the ceiling with it.

If you add tests here, keep an eye on the per-shard time the runner prints. Prefer a richer
body inside an existing `TEST_F` over a new one: the fixture cycle, not the body, is what costs.

## 2. Two defects in the shared CEC mock had to be repaired before the port map was testable

`entservices-testframework/Tests/mocks/HdmiCec.h` is shared by both plugins and both levels.
Two of its declarations made whole clusters of sink code unreachable or fatal, and both were
repaired as part of closing the L2 coverage gap:

* **`ReportPhysicalAddress(const CECFrame&, int startPos)` defaulted to `0`, not `2`.** A CEC
  frame's operands start at byte 2 — byte 0 is the header and byte 1 the opcode — and
  `MessageDecoder` relies on that default. So every inbound `<Report Physical Address>` parsed
  its physical address out of `{header, opcode}`: for `4F 84 10 00 04` the address came out as
  `{0x4F, 0x84}` instead of `{0x10, 0x00}`. Exactly 2 of the 42 frame-parsing constructors in
  that header used `0`; the other 40 already used `2`.
* **`PhysicalAddress` stored digit-constructed and frame-parsed addresses in incompatible
  layouts.** The four-digit constructor pushed four raw bytes while the frame constructor packed
  two, and `getByteValue()` returned a raw byte rather than a nibble. `HdmiPortMap` builds its
  own `m_physicalAddr` from digits and learns its own logical address only by comparing that
  against a parsed address (`HdmiCecSinkImplementation.h:320`), so the comparison could never
  hold, `m_logicalAddr` stayed `UNREGISTERED` for ever, and `addChild()`, `removeChild()` and
  `getRoute()` — all guarded on it — were dead code no test at any level could reach through the
  production frame path. The constructor now packs nibbles the way `ccec`'s real
  `PhysicalAddress` does, `getByteValue()` unpacks digit *n*, and a `toString()` override keeps
  the rendered string byte-identical to the old layout so existing assertions still hold.

**Still defective, deliberately not changed** (no coverage gap requires it, so it is reported
rather than fixed): `SetStreamPath(const CECFrame&, int startPos)` has the same `startPos = 0`
mistake, and `PhysicalAddress(std::string&)` has an empty body, so `setActivePath("2.0.0.0")`
yields an empty address.

## 3. `AbortReason::impl` was uninitialised, and a directed `<Feature Abort>` segfaulted the host

`AbortReason(int)` did not initialise its raw `impl` pointer, and `toInt()` dereferences it
whenever it is non-null. `FeatureAbort(const CECFrame&, int)` builds its `reason` from an int,
so a single **directed** `<Feature Abort>` injected by any test crashed the whole WPEFramework
process inside `HdmiCecSinkProcessor::process(const FeatureAbort&, const Header&)`. Only the
default constructor initialised the pointer. It stayed hidden because broadcast aborts return
early and the registered-source arm sits behind a short-circuit, so no existing test had ever
injected a directed abort from a registered logical address. Now initialised to `nullptr`.

`AbortReasonImplMock` in `Tests/mocks/devicesettings/HdmiCecMock.h` is declared but never wired
to anything, so `impl` is dead scaffolding; nulling it changes no intended behaviour.

## 4. Physical addresses decide which HDMI port a device lands on

`updateDeviceChain()` places an announced address on the port whose `m_portID + 1` equals the
address's **first digit**, so `1.x.x.x` lives on port 0, `2.x.x.x` on port 1 and `3.x.x.x` on
port 2. `onHdmiHotPlug(portId, false)` removes only `hdmiInputs[portId].m_logicalAddr` — the
device sitting directly on that port. Unplugging a port that no announcement matched removes
nothing, silently. If a removal assertion fails, check the `addr = N, portID = M` lines that
`updateDeviceChain` logs before assuming the production code is wrong.
