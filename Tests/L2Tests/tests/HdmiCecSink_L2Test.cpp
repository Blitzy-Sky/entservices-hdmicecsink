/*
 * If not stated otherwise in this file or this component's LICENSE file the
 * following copyright and licenses apply:
 *
 * Copyright 2025 RDK Management
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
#include "L2Tests.h"
#include "L2TestsMock.h"
#include <algorithm>
#include <condition_variable>
#include <fstream>
#include <vector>
#include <functional>
#include <mutex>
#include <utility>
#include <gmock/gmock.h>
#include <gtest/gtest.h>
#include <interfaces/IHdmiCecSink.h>
// Used to change the power state for onpowermodechanged event
#include <interfaces/IPowerManager.h>

#define EVNT_TIMEOUT (5000)
#define HDMICECSINK_CALLSIGN _T("org.rdk.HdmiCecSink.1")
#define HDMICECSINK_L2TEST_CALLSIGN _T("L2tests.1")

#define TEST_LOG(x, ...)                                                                                                                         \
    fprintf(stderr, "\033[1;32m[%s:%d](%s)<PID:%d><TID:%d>" x "\n\033[0m", __FILE__, __LINE__, __FUNCTION__, getpid(), gettid(), ##__VA_ARGS__); \
    fflush(stderr);

using ::testing::NiceMock;
using namespace WPEFramework;
using testing::StrictMock;
using HdmiCecSinkSuccess = WPEFramework::Exchange::IHdmiCecSink::HdmiCecSinkSuccess;
using HdmiCecSinkDevice = WPEFramework::Exchange::IHdmiCecSink::HdmiCecSinkDevices;
using HdmiCecSinkActivePath = WPEFramework::Exchange::IHdmiCecSink::HdmiCecSinkActivePath;
using IHdmiCecSinkDeviceListIterator = WPEFramework::Exchange::IHdmiCecSink::IHdmiCecSinkDeviceListIterator;
using IHdmiCecSinkActivePathIterator = WPEFramework::Exchange::IHdmiCecSink::IHdmiCecSinkActivePathIterator;
using PowerState = WPEFramework::Exchange::IPowerManager::PowerState;

namespace {
static void removeFile(const char* fileName)
{
    // Use sudo for protected files
    if (strcmp(fileName, "/etc/device.properties") == 0 || strcmp(fileName, "/opt/persistent/ds/cecData_2.json") == 0 || strcmp(fileName, "/opt/uimgr_settings.bin") == 0) {
        char cmd[256];
        snprintf(cmd, sizeof(cmd), "sudo rm -f %s", fileName);
        int ret = system(cmd);
        if (ret != 0) {
            printf("File %s failed to remove with sudo\n", fileName);
            perror("Error deleting file");
        } else {
            printf("File %s successfully deleted with sudo\n", fileName);
        }
    } else {
        if (std::remove(fileName) != 0) {
            printf("File %s failed to remove\n", fileName);
            perror("Error deleting file");
        } else {
            printf("File %s successfully deleted\n", fileName);
        }
    }
}

static void createFile(const char* fileName, const char* fileContent)
{
    std::ofstream fileContentStream(fileName);
    fileContentStream << fileContent;
    fileContentStream << "\n";
    fileContentStream.close();
}
}

// Event flags for different CEC events
typedef enum : uint32_t {
    ON_ACTIVE_SOURCE_CHANGE = 0x00000001,
    ON_DEVICE_ADDED = 0x00000002,
    ON_DEVICE_REMOVED = 0x00000004,
    ON_DEVICE_INFO_UPDATED = 0x00000008,
    ON_IMAGE_VIEW_ON = 0x00000010,
    ON_TEXT_VIEW_ON = 0x00000020,
    ON_INACTIVE_SOURCE = 0x00000040,
    ON_WAKEUP_FROM_STANDBY = 0x00000080,
    ARC_INITIATION_EVENT = 0x00000100,
    ARC_TERMINATION_EVENT = 0x00000200,
    REPORT_AUDIO_DEVICE_CONNECTED = 0x00000400,
    // Each event needs a bit of its own: handlers OR their bit into m_event_signalled and
    // WaitForRequestStatus() tests the expected event against that accumulated mask.
    ON_KEY_PRESS_EVENT = 0x00000800,
    ON_KEY_RELEASE_EVENT = 0x00001000,
    ON_REPORT_AUDIO_STATUS = 0x10000000,
    REPORT_FEATURE_ABORT = 0x20000000,
    REPORT_CEC_ENABLED = 0x40000000,
    ON_SET_SYSTEM_AUDIO_MODE = 0x80000000,
    SHORT_AUDIO_DESCRIPTOR = 0x00008000,
    STANDBY_MESSAGE_RECEIVED = 0x00010000,
    REPORT_AUDIO_DEVICE_POWER_STATUS = 0x00020000,
    HDMICECSINK_STATUS_INVALID = 0x00000000
} HdmiCecSinkL2test_async_events_t;

//=====================================================================================
// LATENT CONDITIONS IN THIS SUITE - REPORTED, NOT FIXED
//
// These are recorded here rather than in COVERAGE_TRACEABILITY_REPORT.md because that report does
// not exist at this milestone, and an observation that lives nowhere is an observation that gets
// lost. Each entry is a real property of the code as it stands, verified in this tree; none of them
// currently makes the suite fail, and each would take a change wider than its value to remove.
//
// 1. HdmiCecSinkNotificationHandler::m_event_signalled is declared but NOT initialised by the
//    constructor below, which lists only m_logicalAddress and m_keyCode. Every read goes through
//    WaitForRequestStatus, which masks with the caller's expected bits, so an indeterminate initial
//    value could in principle satisfy a wait that nothing signalled. It has not been observed: the
//    tests that use the direct COM-RPC route assert the payload as well as the flag, so a spurious
//    flag alone cannot make one of them pass. Fixing it means touching the constructor of a type
//    that every passing test in this file shares.
//
// 2. Four negative cases (InjectImageViewOnFrameBroadcastAndVerifyNoEvent,
//    InjectTextViewOnFrameBroadcastAndVerifyNoEvent,
//    InjectImageViewOnFromUnregisteredAddressAndVerifyNoEvent,
//    InjectTextViewOnFromUnregisteredAddressAndVerifyNoEvent) each block for the full 5000 ms
//    EVNT_TIMEOUT proving an absence, about 20 s of the suite's wall time. The production fan-out
//    they are asserting against runs synchronously inside listener->notify(), so a much shorter
//    grace would be sound - InjectFeatureAbortFrameBroadcastAndVerifyNoEvent below uses 1500 ms and
//    explains why - but shortening the existing four means editing tests that pass.
//
// 3. HdmiHotplugDisconnectAndVerifyDeviceRemovedEvent depends on the asynchronous poll sweep
//    reacting to the re-armed throwing ping() within EVNT_TIMEOUT. It announces every peer first so
//    at least one is present whatever the sweep's phase, which makes it robust rather than lucky,
//    but the pass is still timing-dependent rather than causally forced.
//
// 4. Several tests read the fixture members m_logicalAddress/m_keyCode, which the JSON-RPC
//    dispatchers write from the Thunder notification thread, without holding the fixture's m_mutex.
//    The happens-before edge supplied by WaitForRequestStatus makes this safe in practice. The
//    handler's own accessors have been given the lock (see below); the fixture-level members are
//    read directly by existing passing test bodies and are left alone.
//
// 5. The suite reaches the plugin only through JSON-RPC and COM-RPC, so implementation state that no
//    registered method exposes cannot be asserted at this level at all - for example the CEC-version
//    and m_featureAborts bookkeeping a directed Feature Abort performs. That one is compounded by a
//    mock defect which makes the directed frame crash outright; both are set out in full at the
//    reportFeatureAbortEvent note further down. Such state belongs in L1, where
//    HdmiCecSinkImplementation::_instance is reachable.
//=====================================================================================
/*
 * Runs its action when it goes out of scope, whatever the reason.
 *
 * The registrations and subscriptions these tests make are process-wide: a COM-RPC
 * notification sink handed to the plugin, and a JSON-RPC event subscription held by the
 * dispatcher. Undoing them with statements at the end of a test body means a fatal
 * ASSERT_* - which returns from the test function immediately - leaves the plugin holding a
 * pointer to a sink that is about to be destroyed, and leaves the subscription in place for
 * every later case. Binding the undo to a scope makes it unskippable.
 */
class ScopedCleanup {
public:
    explicit ScopedCleanup(std::function<void()> action)
        : m_action(std::move(action))
    {
    }

    ScopedCleanup(const ScopedCleanup&) = delete;
    ScopedCleanup& operator=(const ScopedCleanup&) = delete;

    ~ScopedCleanup()
    {
        if (m_action) {
            m_action();
        }
    }

private:
    std::function<void()> m_action;
};

// Notification handler for HdmiCecSink events
class HdmiCecSinkNotificationHandler : public Exchange::IHdmiCecSink::INotification {
private:
    // mutable so the payload accessors below can be const and still take the lock: every handler
    // runs on a plugin thread, so an unsynchronised read of the recorded payload is a data race.
    mutable std::mutex m_mutex;
    std::condition_variable m_condition_variable;
    uint32_t m_event_signalled;
    int m_logicalAddress;
    int m_keyCode;
    int m_imageViewOnLogicalAddress;
    int m_removedLogicalAddress;
    int m_featureAbortLogicalAddress;
    int m_featureAbortOpcode;
    int m_featureAbortReason;
    std::vector<int> m_removedLogicalAddresses;

    BEGIN_INTERFACE_MAP(Notification)
    INTERFACE_ENTRY(Exchange::IHdmiCecSink::INotification)
    END_INTERFACE_MAP

public:
    // m_event_signalled is the bit mask every callback ORs into and WaitForRequestStatus reads, so
    // it MUST start from a known value: reading an uninitialised member is undefined behaviour, and
    // in practice a stale non-zero bit makes a wait return immediately (a spurious pass) while a
    // stale zero makes the caller wait out its whole timeout. HDMICECSINK_STATUS_INVALID is the
    // "no event yet" value used throughout this file, and it is the same initialisation the sibling
    // fixture uses for its own mask.
    HdmiCecSinkNotificationHandler()
        : m_event_signalled(HDMICECSINK_STATUS_INVALID)
        , m_logicalAddress(0)
        , m_keyCode(0)
        , m_imageViewOnLogicalAddress(-1)
        , m_removedLogicalAddress(-1)
        , m_featureAbortLogicalAddress(-1)
        , m_featureAbortOpcode(-1)
        , m_featureAbortReason(-1)
    {
    }
    ~HdmiCecSinkNotificationHandler() {}

    // This handler lives as a fixture member and is therefore reused across the cases in a
    // fixture. Clearing the accumulated flags under the same mutex the handlers take gives each
    // registration a known starting point, so an assertion can never observe an event that a
    // previous case signalled.
    void ResetEvents()
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled = HDMICECSINK_STATUS_INVALID;
        m_logicalAddress = 0;
        m_keyCode = 0;
    }

    // Event handlers with data storage for validation
    void ArcInitiationEvent(const string status) override
    {
        TEST_LOG("ArcInitiationEvent triggered with status: %s", status.c_str());
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled |= ARC_INITIATION_EVENT;
        m_condition_variable.notify_one();
    }

    void ArcTerminationEvent(const string status) override
    {
        TEST_LOG("ArcTerminationEvent triggered with status: %s", status.c_str());
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled |= ARC_TERMINATION_EVENT;
        m_condition_variable.notify_one();
    }

    void OnActiveSourceChange(const int logicalAddress, const string physicalAddress) override
    {
        TEST_LOG("OnActiveSourceChange event: logicalAddress=%d, physicalAddress=%s", logicalAddress, physicalAddress.c_str());
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled |= ON_ACTIVE_SOURCE_CHANGE;
        m_condition_variable.notify_one();
    }

    void OnDeviceAdded(const int logicalAddress) override
    {
        TEST_LOG("OnDeviceAdded triggered - logicalAddress: %d", logicalAddress);
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled |= ON_DEVICE_ADDED;
        m_condition_variable.notify_one();
    }

    void OnDeviceInfoUpdated(const int logicalAddress) override
    {
        TEST_LOG("OnDeviceInfoUpdated triggered - logicalAddress: %d", logicalAddress);
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled |= ON_DEVICE_INFO_UPDATED;
        m_condition_variable.notify_one();
    }

    void OnDeviceRemoved(const int logicalAddress) override
    {
        TEST_LOG("OnDeviceRemoved triggered - logicalAddress: %d", logicalAddress);
        std::unique_lock<std::mutex> lock(m_mutex);
        // The payload is kept, not just the event bit: an event that fires for the wrong device
        // is a defect, and a test that only checks the bit cannot see it.
        m_removedLogicalAddress = logicalAddress;
        // A single poll sweep removes EVERY device that stopped acknowledging, so it emits one event
        // per device and a "last address" reading is not on its own assertable. Keeping the whole
        // set lets a test assert that a device it knows was present is among those reported.
        m_removedLogicalAddresses.push_back(logicalAddress);
        m_event_signalled |= ON_DEVICE_REMOVED;
        m_condition_variable.notify_one();
    }

    void OnImageViewOnMsg(const int logicalAddress) override
    {
        TEST_LOG("OnImageViewOnMsg triggered - logicalAddress: %d", logicalAddress);
        std::unique_lock<std::mutex> lock(m_mutex);
        m_imageViewOnLogicalAddress = logicalAddress;
        m_event_signalled |= ON_IMAGE_VIEW_ON;
        m_condition_variable.notify_one();
    }

    void OnInActiveSource(const int logicalAddress, const string physicalAddress) override
    {
        TEST_LOG("OnInActiveSource triggered - logicalAddress: %d, physicalAddress: %s",
            logicalAddress, physicalAddress.c_str());
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled |= ON_INACTIVE_SOURCE;
        m_condition_variable.notify_one();
    }

    void OnTextViewOnMsg(const int logicalAddress) override
    {
        TEST_LOG("OnTextViewOnMsg triggered - logicalAddress: %d", logicalAddress);
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled |= ON_TEXT_VIEW_ON;
        m_condition_variable.notify_one();
    }

    void OnWakeupFromStandby(const int logicalAddress) override
    {
        TEST_LOG("OnWakeupFromStandby triggered - logicalAddress: %d", logicalAddress);
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled |= ON_WAKEUP_FROM_STANDBY;
        m_condition_variable.notify_one();
    }

    void ReportAudioDeviceConnectedStatus(const string status, const string audioDeviceConnected) override
    {
        TEST_LOG("ReportAudioDeviceConnectedStatus - status: %s, connected: %s",
            status.c_str(), audioDeviceConnected.c_str());
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled |= REPORT_AUDIO_DEVICE_CONNECTED;
        m_condition_variable.notify_one();
    }

    void ReportAudioStatusEvent(const int muteStatus, const int volumeLevel) override
    {
        TEST_LOG("ReportAudioStatusEvent - muteStatus: %d, volumeLevel: %d", muteStatus, volumeLevel);
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled |= ON_REPORT_AUDIO_STATUS;
        m_condition_variable.notify_one();
    }

    void ReportFeatureAbortEvent(const int logicalAddress, const int opcode, const int FeatureAbortReason) override
    {
        TEST_LOG("ReportFeatureAbortEvent - logicalAddress: %d, opcode: %d, reason: %d",
            logicalAddress, opcode, FeatureAbortReason);
        std::unique_lock<std::mutex> lock(m_mutex);
        m_featureAbortLogicalAddress = logicalAddress;
        m_featureAbortOpcode = opcode;
        m_featureAbortReason = FeatureAbortReason;
        m_event_signalled |= REPORT_FEATURE_ABORT;
        m_condition_variable.notify_one();
    }

    void ReportCecEnabledEvent(const string cecEnable) override
    {
        TEST_LOG("ReportCecEnabledEvent - cecEnable: %s", cecEnable.c_str());
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled |= REPORT_CEC_ENABLED;
        m_condition_variable.notify_one();
    }

    void SetSystemAudioModeEvent(const string audioMode) override
    {
        TEST_LOG("SetSystemAudioModeEvent - audioMode: %s", audioMode.c_str());
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled |= ON_SET_SYSTEM_AUDIO_MODE;
        m_condition_variable.notify_one();
    }

    void ShortAudiodescriptorEvent(const string& jsonresponse) override
    {
        TEST_LOG("ShortAudiodescriptorEvent - jsonResponse: %s", jsonresponse.c_str());
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled |= SHORT_AUDIO_DESCRIPTOR;
        m_condition_variable.notify_one();
    }

    void StandbyMessageReceived(const int logicalAddress) override
    {
        TEST_LOG("StandbyMessageReceived - logicalAddress: %d", logicalAddress);
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled |= STANDBY_MESSAGE_RECEIVED;
        m_condition_variable.notify_one();
    }

    void ReportAudioDevicePowerStatus(const int powerStatus) override
    {
        TEST_LOG("ReportAudioDevicePowerStatus - powerStatus: %d", powerStatus);
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled |= REPORT_AUDIO_DEVICE_POWER_STATUS;
        m_condition_variable.notify_one();
    }

    void OnKeyPressEvent(const int logicalAddress, const int keyCode) override
    {
        TEST_LOG("OnKeyPressEvent event received, logicalAddress: %d, keyCode: %d", logicalAddress, keyCode);
        std::unique_lock<std::mutex> lock(m_mutex);
        m_logicalAddress = logicalAddress;
        m_keyCode = keyCode;
        m_event_signalled |= ON_KEY_PRESS_EVENT;
        m_condition_variable.notify_one();
    }

    void OnKeyReleaseEvent(const int logicalAddress) override
    {
        TEST_LOG("OnKeyReleaseEvent event received, logicalAddress: %d", logicalAddress);
        std::unique_lock<std::mutex> lock(m_mutex);
        m_logicalAddress = logicalAddress;
        m_event_signalled |= ON_KEY_RELEASE_EVENT;
        m_condition_variable.notify_one();
    }

    // The payloads are written by the COM-RPC notification thread and read by the test thread, so
    // both accessors take the same lock the notification overrides above take. In practice a
    // happens-before edge already exists, because a caller reaches these only after
    // WaitForRequestStatus() has acquired and released m_mutex - but relying on that makes the
    // accessors correct only by virtue of how they happen to be called. m_mutex is made mutable so
    // the const contract of the accessors is preserved rather than dropped to buy the lock.
    int GetLogicalAddress() const
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        return m_logicalAddress;
    }

    int GetKeyCode() const
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        return m_keyCode;
    }

    int GetImageViewOnLogicalAddress() const
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        return m_imageViewOnLogicalAddress;
    }

    int GetRemovedLogicalAddress() const
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        return m_removedLogicalAddress;
    }

    std::vector<int> GetRemovedLogicalAddresses() const
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        return m_removedLogicalAddresses;
    }

    int GetFeatureAbortLogicalAddress() const
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        return m_featureAbortLogicalAddress;
    }

    int GetFeatureAbortOpcode() const
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        return m_featureAbortOpcode;
    }

    int GetFeatureAbortReason() const
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        return m_featureAbortReason;
    }

    // Puts every recorded field and the event mask back to their constructed values, so a test
    // measures its own window rather than whatever a previous test left behind.
    void ResetEvent()
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled = HDMICECSINK_STATUS_INVALID;
        m_logicalAddress = 0;
        m_keyCode = 0;
        m_imageViewOnLogicalAddress = -1;
        m_removedLogicalAddress = -1;
        m_featureAbortLogicalAddress = -1;
        m_featureAbortOpcode = -1;
        m_featureAbortReason = -1;
        m_removedLogicalAddresses.clear();
    }

    uint32_t WaitForRequestStatus(uint32_t timeout_ms, HdmiCecSinkL2test_async_events_t expected_status)
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        auto now = std::chrono::system_clock::now();
        std::chrono::milliseconds timeout(timeout_ms);
        uint32_t signalled = HDMICECSINK_STATUS_INVALID;

        while (!(expected_status & m_event_signalled)) {
            if (m_condition_variable.wait_until(lock, now + timeout) == std::cv_status::timeout) {
                TEST_LOG("Timeout waiting for request status event");
                break;
            }
        }
        signalled = m_event_signalled;
        // Clear only the expected flags that were waited for, not all flags
        m_event_signalled &= ~expected_status;
        return signalled;
    }
};

class AsyncHandlerMock_HdmiCecSink {
public:
    AsyncHandlerMock_HdmiCecSink()
    {
    }

    MOCK_METHOD(void, arcInitiationEvent, (const JsonObject& message));
    MOCK_METHOD(void, arcTerminationEvent, (const JsonObject& message));
    MOCK_METHOD(void, onActiveSourceChange, (const JsonObject& message));
    MOCK_METHOD(void, onDeviceAdded, (const JsonObject& message));
    MOCK_METHOD(void, onDeviceInfoUpdated, (const JsonObject& message));
    MOCK_METHOD(void, onDeviceRemoved, (const JsonObject& message));
    MOCK_METHOD(void, onImageViewOnMsg, (const JsonObject& message));
    MOCK_METHOD(void, onInActiveSource, (const JsonObject& message));
    MOCK_METHOD(void, onTextViewOnMsg, (const JsonObject& message));
    MOCK_METHOD(void, onWakeupFromStandby, (const JsonObject& message));
    MOCK_METHOD(void, reportAudioDeviceConnectedStatus, (const JsonObject& message));
    MOCK_METHOD(void, reportAudioStatusEvent, (const JsonObject& message));
    MOCK_METHOD(void, reportFeatureAbortEvent, (const JsonObject& message));
    MOCK_METHOD(void, reportCecEnabledEvent, (const JsonObject& message));
    MOCK_METHOD(void, setSystemAudioModeEvent, (const JsonObject& message));
    MOCK_METHOD(void, shortAudiodescriptorEvent, (const JsonObject& message));
    MOCK_METHOD(void, standbyMessageReceived, (const JsonObject& message));
    MOCK_METHOD(void, reportAudioDevicePowerStatus, (const JsonObject& message));
    MOCK_METHOD(void, onKeyPressEvent, (const JsonObject& message));
    MOCK_METHOD(void, onKeyReleaseEvent, (const JsonObject& message));
};

class HdmiCecSink_L2Test : public L2TestMocks {
protected:
    HdmiCecSink_L2Test();
    virtual ~HdmiCecSink_L2Test() override;
    virtual void SetUp() override;
    virtual void TearDown() override;

public:
    uint32_t CreateHdmiCecSinkInterfaceObject();
    uint32_t WaitForRequestStatus(uint32_t timeout_ms, HdmiCecSinkL2test_async_events_t expected_status);
    void arcInitiationEvent(const JsonObject& message);
    void arcTerminationEvent(const JsonObject& message);
    void onActiveSourceChange(const JsonObject& message);
    void onDeviceAdded(const JsonObject& message);
    void onDeviceInfoUpdated(const JsonObject& message);
    void onDeviceRemoved(const JsonObject& message);
    void onImageViewOnMsg(const JsonObject& message);
    void onInActiveSource(const JsonObject& message);
    void onTextViewOnMsg(const JsonObject& message);
    void reportAudioDeviceConnectedStatus(const JsonObject& message);
    void reportAudioStatusEvent(const JsonObject& message);
    void reportFeatureAbortEvent(const JsonObject& message);
    void reportCecEnabledEvent(const JsonObject& message);
    void setSystemAudioModeEvent(const JsonObject& message);
    void shortAudiodescriptorEvent(const JsonObject& message);
    void standbyMessageReceived(const JsonObject& message);
    void reportAudioDevicePowerStatus(const JsonObject& message);
    void onKeyPressEvent(const JsonObject& message);
    void onKeyReleaseEvent(const JsonObject& message);

protected:
    Exchange::IHdmiCecSink* m_cecSinkPlugin = nullptr;
    PluginHost::IShell* m_controller_cecSink = nullptr;
    Core::Sink<HdmiCecSinkNotificationHandler> m_notificationHandler;
    IARM_EventHandler_t dsHdmiEventHandler;
    IARM_EventHandler_t powerEventHandler = nullptr;
    FrameListener* registeredListener = nullptr;
    std::vector<FrameListener*> listeners;
    device::Host::IHdmiInEvents* g_registeredHdmiInListener = nullptr;
    int m_logicalAddress = 0;
    int m_keyCode = 0;
    /* Payloads captured from the JSON-RPC notifications, so a test can assert WHICH device an
       event named rather than only that some event arrived. Read through the accessors below,
       which take the same mutex the callbacks hold. */
    int m_jsonImageViewOnLogicalAddress = -1;
    int m_jsonRemovedLogicalAddress = -1;
    int m_jsonFeatureAbortLogicalAddress = -1;
    int m_jsonFeatureAbortOpcode = -1;
    int m_jsonFeatureAbortReason = -1;
    std::vector<int> m_jsonRemovedLogicalAddresses;

    int JsonImageViewOnLogicalAddress()
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        return m_jsonImageViewOnLogicalAddress;
    }

    int JsonRemovedLogicalAddress()
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        return m_jsonRemovedLogicalAddress;
    }

    std::vector<int> JsonRemovedLogicalAddresses()
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        return m_jsonRemovedLogicalAddresses;
    }

    int JsonFeatureAbortLogicalAddress()
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        return m_jsonFeatureAbortLogicalAddress;
    }

    int JsonFeatureAbortOpcode()
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        return m_jsonFeatureAbortOpcode;
    }

    int JsonFeatureAbortReason()
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        return m_jsonFeatureAbortReason;
    }

    void ResetJsonEventState()
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        m_event_signalled = HDMICECSINK_STATUS_INVALID;
        m_jsonImageViewOnLogicalAddress = -1;
        m_jsonRemovedLogicalAddress = -1;
        m_jsonFeatureAbortLogicalAddress = -1;
        m_jsonFeatureAbortOpcode = -1;
        m_jsonFeatureAbortReason = -1;
        m_jsonRemovedLogicalAddresses.clear();
    }

    // ---- cleanup scope guards ---------------------------------------------------------------
    // These tests use FATAL assertions (ASSERT_*) after they have taken out a JSON-RPC
    // subscription and a COM-RPC interface. A fatal assertion returns from the test body on the
    // spot, so an Unsubscribe/Unregister/Release written as a trailing statement never runs: the
    // next test then inherits a live subscription and a leaked interface, and the handler that
    // subscription points at is a local of a function that has already returned. Putting the
    // cleanup in destructors makes it run on every exit path, including that one.
    class JsonRpcSubscription {
    public:
        JsonRpcSubscription(JSONRPC::LinkType<Core::JSON::IElement>& link, const string& eventName)
            : m_link(link)
            , m_eventName(eventName)
        {
        }

        ~JsonRpcSubscription()
        {
            m_link.Unsubscribe(EVNT_TIMEOUT, m_eventName);
        }

        JsonRpcSubscription(const JsonRpcSubscription&) = delete;
        JsonRpcSubscription& operator=(const JsonRpcSubscription&) = delete;

    private:
        JSONRPC::LinkType<Core::JSON::IElement>& m_link;
        string m_eventName;
    };

    // Releases the COM-RPC interface pair the fixture holds and clears the fixture's pointers, so
    // nothing dangling is left behind for the next test either.
    // ---- discovery quiescing ----------------------------------------------------------------
    // Every notification fan-out in HdmiCecSinkImplementation walks _hdmiCecSinkNotifications
    // WITHOUT holding _adminLock - the lock is taken in Register() and Unregister() only, and those
    // are the sole four uses of it in the whole implementation - while Register()/Unregister()
    // mutate that same std::list under it. So attaching or detaching a COM notification WHILE the
    // poll thread's discovery sweep is fanning OnDeviceAdded/ReportAudioDeviceConnectedStatus out
    // races the list: Unregister erases the element the sweep is iterating and Releases the proxy it
    // is about to call, and the plugin host takes SIGSEGV. That is a PRODUCTION defect; Directive 6
    // forbids fixing it from here, so it is reported instead and these tests simply decline to
    // provoke it - a notification is attached and detached only while discovery is quiet.
    //
    // Quiet is observed through the public COM GetDeviceList(), which reports the count the sweep is
    // populating; no unsynchronised production state is read. Two consecutive equal readings,
    // separated by a sample interval, mark a window in which the sweep added nothing - and since a
    // completed sweep then parks for HDMICECSINK_PING_INTERVAL_MS, that window is wide. The wait is
    // bounded and reports its own expiry rather than hanging.
    static bool WaitForDiscoveryToSettle(Exchange::IHdmiCecSink* plugin, uint32_t timeoutMs = 8000)
    {
        const uint32_t kSampleIntervalMs = 150;
        const uint32_t kSettledSamples = 2;

        if (plugin == nullptr) {
            return false;
        }

        uint32_t previousCount = 0;
        bool havePrevious = false;
        uint32_t stableSamples = 0;
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeoutMs);

        while (std::chrono::steady_clock::now() < deadline) {
            uint32_t numberOfDevices = 0;
            bool success = false;
            IHdmiCecSinkDeviceListIterator* deviceList = nullptr;

            if (plugin->GetDeviceList(numberOfDevices, deviceList, success) != Core::ERROR_NONE) {
                return false;
            }
            if (deviceList != nullptr) {
                deviceList->Release();
            }

            if (havePrevious && (numberOfDevices == previousCount)) {
                if (++stableSamples >= kSettledSamples) {
                    return true;
                }
            } else {
                stableSamples = 0;
            }
            previousCount = numberOfDevices;
            havePrevious = true;

            std::this_thread::sleep_for(std::chrono::milliseconds(kSampleIntervalMs));
        }
        return false;
    }

    class SinkInterfaceScope {
    public:
        SinkInterfaceScope(Exchange::IHdmiCecSink*& plugin,
            PluginHost::IShell*& controller,
            Exchange::IHdmiCecSink::INotification* notification)
            : m_plugin(plugin)
            , m_controller(controller)
            , m_notification(notification)
        {
        }

        ~SinkInterfaceScope()
        {
            if (m_plugin != nullptr) {
                // Detach only while discovery is quiet - see WaitForDiscoveryToSettle above for the
                // production defect this avoids. A failure to settle is not fatal here: the
                // destructor must still release what it owns, and the test body's own assertions
                // are what report a misbehaving sweep.
                WaitForDiscoveryToSettle(m_plugin);
                m_plugin->Unregister(m_notification);
                m_plugin->Release();
                m_plugin = nullptr;
            }
            if (m_controller != nullptr) {
                m_controller->Release();
                m_controller = nullptr;
            }
        }

        SinkInterfaceScope(const SinkInterfaceScope&) = delete;
        SinkInterfaceScope& operator=(const SinkInterfaceScope&) = delete;

    private:
        Exchange::IHdmiCecSink*& m_plugin;
        PluginHost::IShell*& m_controller;
        Exchange::IHdmiCecSink::INotification* m_notification;
    };

    /**
     * Bring CEC up so that the production inbound frame path is live, and wait until the
     * implementation has registered its FrameListener.
     *
     * The CEC-enabled setting is persisted by the implementation and therefore survives plugin
     * deactivation, so it is shared state across this whole suite: Set_And_Get_Enabled_JSONRPC
     * legitimately leaves it false, and every activation after that one loads CEC_SETTING_ENABLED
     * as 0. With CEC disabled the implementation never opens the connection, so addFrameListener
     * is never called and @c listeners stays empty. A test that must exercise inbound frames has
     * to establish that precondition for itself instead of inheriting it from whichever test
     * happened to run immediately before it.
     *
     * Registration is asynchronous with respect to the setEnabled call, so the wait polls rather
     * than assuming the listener is in place on return.
     *
     * @param timeoutMs Upper bound, in milliseconds, on the wait for the registration.
     * @return true when at least one FrameListener has been captured.
     */
    bool EnableCecAndAwaitFrameListener(const uint32_t timeoutMs = 5000)
    {
        if (!listeners.empty()) {
            return true;
        }

        JsonObject params, result;
        if (InvokeServiceMethod("org.rdk.HdmiCecSink", "getEnabled", params, result) == Core::ERROR_NONE) {
            m_cecEnabledByHelper = (result.HasLabel("enabled") && (result["enabled"].Boolean() == false));
        }

        params["enabled"] = true;
        if (InvokeServiceMethod("org.rdk.HdmiCecSink", "setEnabled", params, result) != Core::ERROR_NONE) {
            return false;
        }

        const uint32_t pollIntervalMs = 20;
        for (uint32_t waitedMs = 0; waitedMs <= timeoutMs; waitedMs += pollIntervalMs) {
            if (!listeners.empty()) {
                return true;
            }
            usleep(pollIntervalMs * 1000);
        }

        return !listeners.empty();
    }

    /**
     * Put the persisted CEC-enabled setting back the way this test found it.
     *
     * Called from TearDown so that a test which had to switch CEC on does not hand a different
     * starting state to whatever runs next - the setting is process-global and outlives the
     * plugin, so capture-and-restore is the only way to keep these tests independent of each
     * other. Restoration only happens when this fixture is the party that changed the value.
     */
    void RestoreCecEnabledState()
    {
        if (!m_cecEnabledByHelper) {
            return;
        }

        JsonObject params, result;
        params["enabled"] = false;
        InvokeServiceMethod("org.rdk.HdmiCecSink", "setEnabled", params, result);
        m_cecEnabledByHelper = false;
    }

    Core::ProxyType<RPC::InvokeServerType<1, 0, 4>> HdmiCecSink_Engine;
    Core::ProxyType<RPC::CommunicatorClient> HdmiCecSink_Client;

private:
    std::mutex m_mutex;
    std::condition_variable m_condition_variable;
    uint32_t m_event_signalled = HDMICECSINK_STATUS_INVALID;
    bool m_cecEnabledByHelper = false;
};

HdmiCecSink_L2Test::HdmiCecSink_L2Test()
    : L2TestMocks()
{
    uint32_t status = Core::ERROR_GENERAL;
    createFile("/etc/device.properties", "RDK_PROFILE=TV");
    createFile("/opt/persistent/ds/cecData_2.json", "0");
    createFile("/tmp/pwrmgr_restarted", "2");

    // Add sleep to ensure file is properly written to disk
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    EXPECT_CALL(*p_powerManagerHalMock, PLAT_DS_INIT())
        .WillOnce(::testing::Return(DEEPSLEEPMGR_SUCCESS));

    EXPECT_CALL(*p_powerManagerHalMock, PLAT_INIT())
        .WillRepeatedly(::testing::Return(PWRMGR_SUCCESS));

    EXPECT_CALL(*p_powerManagerHalMock, PLAT_API_SetWakeupSrc(::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return(PWRMGR_SUCCESS));

    EXPECT_CALL(*p_powerManagerHalMock, PLAT_API_GetPowerState(::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [](PWRMgr_PowerState_t* powerState) {
                *powerState = PWRMGR_POWERSTATE_ON; // Default to ON state
                return PWRMGR_SUCCESS;
            }));

    ON_CALL(*p_rfcApiImplMock, getRFCParameter(::testing::_, ::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [](char* pcCallerID, const char* pcParameterName, RFC_ParamData_t* pstParamData) {
                if (strcmp("RFC_DATA_ThermalProtection_POLL_INTERVAL", pcParameterName) == 0) {
                    strcpy(pstParamData->value, "2");
                    return WDMP_SUCCESS;
                } else if (strcmp("RFC_ENABLE_ThermalProtection", pcParameterName) == 0) {
                    strcpy(pstParamData->value, "true");
                    return WDMP_SUCCESS;
                } else if (strcmp("RFC_DATA_ThermalProtection_DEEPSLEEP_GRACE_INTERVAL", pcParameterName) == 0) {
                    strcpy(pstParamData->value, "6");
                    return WDMP_SUCCESS;
                } else if (strcmp("Device.DeviceInfo.X_RDKCENTRAL-COM_RFC.Feature.HdmiCecSink.CECVersion", pcParameterName) == 0) {
                    strncpy(pstParamData->value, "1.4", sizeof(pstParamData->value));
                    return WDMP_SUCCESS;
                } else {
                    /* The default threshold values will assign, if RFC call failed */
                    return WDMP_FAILURE;
                }
            }));

    EXPECT_CALL(*p_mfrMock, mfrSetTempThresholds(::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [](int high, int critical) {
                EXPECT_EQ(high, 100);
                EXPECT_EQ(critical, 110);
                return mfrERR_NONE;
            }));

    EXPECT_CALL(*p_powerManagerHalMock, PLAT_API_SetPowerState(::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [](PWRMgr_PowerState_t powerState) {
                // All tests are run without settings file
                // so default expected power state is ON
                return PWRMGR_SUCCESS;
            }));

    EXPECT_CALL(*p_mfrMock, mfrGetTemperature(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](mfrTemperatureState_t* curState, int* curTemperature, int* wifiTemperature) {
                *curTemperature = 90; // safe temperature
                *curState = (mfrTemperatureState_t)0;
                *wifiTemperature = 25;
                return mfrERR_NONE;
            }));

    ON_CALL(*p_connectionMock, poll(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const LogicalAddress& from, const Throw_e& doThrow) {
                throw CECNoAckException();
            }));

    EXPECT_CALL(*p_libCCECMock, getPhysicalAddress(::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](uint32_t* physAddress) {
                *physAddress = (uint32_t)0x12345678;
            }));

    ON_CALL(*p_messageEncoderMock, encode(::testing::Matcher<const DataBlock&>(::testing::_)))
        .WillByDefault(::testing::ReturnRef(CECFrame::getInstance()));
    ON_CALL(*p_messageEncoderMock, encode(::testing::Matcher<const UserControlPressed&>(::testing::_)))
        .WillByDefault(::testing::ReturnRef(CECFrame::getInstance()));

    ON_CALL(*p_iarmBusImplMock, IARM_Bus_RegisterEventHandler(::testing::_, ::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const char* ownerName, IARM_EventId_t eventId, IARM_EventHandler_t handler) {
                if ((string(IARM_BUS_DSMGR_NAME) == string(ownerName)) && (eventId == IARM_BUS_DSMGR_EVENT_HDMI_IN_HOTPLUG)) {
                    EXPECT_TRUE(handler != nullptr);
                    dsHdmiEventHandler = handler;
                }
                return IARM_RESULT_SUCCESS;
            }));

    ON_CALL(*p_connectionMock, addFrameListener(::testing::_))
        .WillByDefault([this](FrameListener* listener) {
            printf("[TEST] addFrameListener called with address: %p\n", static_cast<void*>(listener));
            this->listeners.push_back(listener);
        });

    EXPECT_CALL(*p_hostImplMock, Register(::testing::A<device::Host::IHdmiInEvents*>()))
        .WillOnce(::testing::Invoke(
            [&](device::Host::IHdmiInEvents* listener) -> dsError_t {
                this->g_registeredHdmiInListener = listener;
                fprintf(stderr, "[TEST MOCK] Host::Register captured listener=%p\n", static_cast<void*>(listener));
                fflush(stderr);
                return static_cast<dsError_t>(0);
            }));

    ON_CALL(*p_connectionMock, open())
        .WillByDefault(::testing::Return());

    EXPECT_CALL(*p_hdmiInputImplMock, getNumberOfInputs())
        .WillRepeatedly(::testing::Return(3));

    ON_CALL(*p_hdmiInputImplMock, isPortConnected(::testing::_))
        .WillByDefault(::testing::Invoke(
            [](int8_t port) {
                return port == 1 ? true : false;
            }));

    EXPECT_CALL(*p_hdmiInputImplMock, getHDMIARCPortId(::testing::_))
        .Times(::testing::AtLeast(1))
        .WillRepeatedly(::testing::Invoke(
            [](int& portId) -> dsError_t {
                fprintf(stderr, "[TEST MOCK] getHDMIARCPortId called (expectation)\n");
                portId = 1;
                return static_cast<dsError_t>(0);
            }));

    /* Activate plugin in constructor */
    status = ActivateService("org.rdk.PowerManager");
    EXPECT_EQ(Core::ERROR_NONE, status);

    status = ActivateService("org.rdk.HdmiCecSink");
    EXPECT_EQ(Core::ERROR_NONE, status);
}

void HdmiCecSink_L2Test::SetUp()
{
    // Reset all event flags before each test to prevent race conditions from stale flags
    std::unique_lock<std::mutex> lock(m_mutex);
    m_event_signalled = HDMICECSINK_STATUS_INVALID;
}

void HdmiCecSink_L2Test::TearDown()
{
    // Hand the next test the CEC-enabled state this one inherited, not the one it needed.
    RestoreCecEnabledState();
}

HdmiCecSink_L2Test::~HdmiCecSink_L2Test()
{
    uint32_t status = Core::ERROR_GENERAL;

    ON_CALL(*p_connectionMock, close())
        .WillByDefault(::testing::Return());

    sleep(5);

    // Deactivate services in reverse order
    status = DeactivateService("org.rdk.HdmiCecSink");
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(*p_powerManagerHalMock, PLAT_TERM())
        .WillOnce(::testing::Return(PWRMGR_SUCCESS));

    EXPECT_CALL(*p_powerManagerHalMock, PLAT_DS_TERM())
        .WillOnce(::testing::Return(DEEPSLEEPMGR_SUCCESS));

    status = DeactivateService("org.rdk.PowerManager");
    EXPECT_EQ(Core::ERROR_NONE, status);

    removeFile("/tmp/pwrmgr_restarted");
    removeFile("/opt/persistent/ds/cecData_2.json");
    removeFile("/opt/uimgr_settings.bin");
}

class HdmiCecSink_L2Test_STANDBY : public L2TestMocks {
protected:
    HdmiCecSink_L2Test_STANDBY();
    virtual void SetUp() override;
    virtual void TearDown() override;
    virtual ~HdmiCecSink_L2Test_STANDBY() override;

public:
    uint32_t CreateHdmiCecSinkInterfaceObject();
    uint32_t WaitForRequestStatus(uint32_t timeout_ms, HdmiCecSinkL2test_async_events_t expected_status);
    void onWakeupFromStandby(const JsonObject& message);

protected:
    Exchange::IHdmiCecSink* m_cecSinkPlugin = nullptr;
    PluginHost::IShell* m_controller_cecSink = nullptr;
    Core::Sink<HdmiCecSinkNotificationHandler> m_notificationHandler;
    IARM_EventHandler_t dsHdmiEventHandler;
    IARM_EventHandler_t powerEventHandler = nullptr;
    FrameListener* registeredListener = nullptr;
    std::vector<FrameListener*> listeners;

    /**
     * Standby-suite counterpart of HdmiCecSink_L2Test::EnableCecAndAwaitFrameListener.
     *
     * The CEC-enabled setting is persisted by the implementation, so it is shared across both
     * suites in this binary; a standby test that injects a frame must establish the precondition
     * for itself rather than inherit whatever the preceding test left behind. See the primary
     * fixture's helper for the full rationale.
     *
     * @param timeoutMs Upper bound, in milliseconds, on the wait for the registration.
     * @return true when at least one FrameListener has been captured.
     */
    bool EnableCecAndAwaitFrameListener(const uint32_t timeoutMs = 5000)
    {
        if (!listeners.empty()) {
            return true;
        }

        JsonObject params, result;
        if (InvokeServiceMethod("org.rdk.HdmiCecSink", "getEnabled", params, result) == Core::ERROR_NONE) {
            m_cecEnabledByHelper = (result.HasLabel("enabled") && (result["enabled"].Boolean() == false));
        }

        params["enabled"] = true;
        if (InvokeServiceMethod("org.rdk.HdmiCecSink", "setEnabled", params, result) != Core::ERROR_NONE) {
            return false;
        }

        const uint32_t pollIntervalMs = 20;
        for (uint32_t waitedMs = 0; waitedMs <= timeoutMs; waitedMs += pollIntervalMs) {
            if (!listeners.empty()) {
                return true;
            }
            usleep(pollIntervalMs * 1000);
        }

        return !listeners.empty();
    }

    /**
     * Put the persisted CEC-enabled setting back the way this test found it.
     *
     * Called from TearDown so that a test which had to switch CEC on does not hand a different
     * starting state to whatever runs next - the setting is process-global and outlives the
     * plugin, so capture-and-restore is the only way to keep these tests independent of each
     * other. Restoration only happens when this fixture is the party that changed the value.
     */
    void RestoreCecEnabledState()
    {
        if (!m_cecEnabledByHelper) {
            return;
        }

        JsonObject params, result;
        params["enabled"] = false;
        InvokeServiceMethod("org.rdk.HdmiCecSink", "setEnabled", params, result);
        m_cecEnabledByHelper = false;
    }

    Core::ProxyType<RPC::InvokeServerType<1, 0, 4>> HdmiCecSink_Engine;
    Core::ProxyType<RPC::CommunicatorClient> HdmiCecSink_Client;

private:
    std::mutex m_mutex;
    std::condition_variable m_condition_variable;
    uint32_t m_event_signalled = HDMICECSINK_STATUS_INVALID;
    bool m_cecEnabledByHelper = false;
};

HdmiCecSink_L2Test_STANDBY::HdmiCecSink_L2Test_STANDBY()
    : L2TestMocks()
{
    uint32_t status = Core::ERROR_GENERAL;
    removeFile("/tmp/pwrmgr_restarted");
    createFile("/etc/device.properties", "RDK_PROFILE=TV");

    // Add sleep to ensure file is properly written to disk
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    EXPECT_CALL(*p_powerManagerHalMock, PLAT_DS_INIT())
        .WillOnce(::testing::Return(DEEPSLEEPMGR_SUCCESS));

    EXPECT_CALL(*p_powerManagerHalMock, PLAT_INIT())
        .WillRepeatedly(::testing::Return(PWRMGR_SUCCESS));

    EXPECT_CALL(*p_powerManagerHalMock, PLAT_API_SetWakeupSrc(::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return(PWRMGR_SUCCESS));

    ON_CALL(*p_rfcApiImplMock, getRFCParameter(::testing::_, ::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [](char* pcCallerID, const char* pcParameterName, RFC_ParamData_t* pstParamData) {
                if (strcmp("RFC_DATA_ThermalProtection_POLL_INTERVAL", pcParameterName) == 0) {
                    strcpy(pstParamData->value, "2");
                    return WDMP_SUCCESS;
                } else if (strcmp("RFC_ENABLE_ThermalProtection", pcParameterName) == 0) {
                    strcpy(pstParamData->value, "true");
                    return WDMP_SUCCESS;
                } else if (strcmp("RFC_DATA_ThermalProtection_DEEPSLEEP_GRACE_INTERVAL", pcParameterName) == 0) {
                    strcpy(pstParamData->value, "6");
                    return WDMP_SUCCESS;
                } else if (strcmp("Device.DeviceInfo.X_RDKCENTRAL-COM_RFC.Feature.HdmiCecSink.CECVersion", pcParameterName) == 0) {
                    strncpy(pstParamData->value, "1.4", sizeof(pstParamData->value));
                    return WDMP_SUCCESS;
                } else {
                    /* The default threshold values will assign, if RFC call failed */
                    return WDMP_FAILURE;
                }
            }));

    EXPECT_CALL(*p_mfrMock, mfrSetTempThresholds(::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [](int high, int critical) {
                EXPECT_EQ(high, 100);
                EXPECT_EQ(critical, 110);
                return mfrERR_NONE;
            }));

    EXPECT_CALL(*p_powerManagerHalMock, PLAT_API_GetPowerState(::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [](PWRMgr_PowerState_t* powerState) {
                *powerState = PWRMGR_POWERSTATE_OFF; // by default over boot up, return PowerState OFF
                return PWRMGR_SUCCESS;
            }));

    EXPECT_CALL(*p_powerManagerHalMock, PLAT_API_SetPowerState(::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [](PWRMgr_PowerState_t powerState) {
                // All tests are run without settings file
                // so default expected power state is ON
                return PWRMGR_SUCCESS;
            }));

    EXPECT_CALL(*p_mfrMock, mfrGetTemperature(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](mfrTemperatureState_t* curState, int* curTemperature, int* wifiTemperature) {
                *curTemperature = 90; // safe temperature
                *curState = (mfrTemperatureState_t)0;
                *wifiTemperature = 25;
                return mfrERR_NONE;
            }));

    ON_CALL(*p_connectionMock, poll(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const LogicalAddress& from, const Throw_e& doThrow) {
                throw CECNoAckException();
            }));

    EXPECT_CALL(*p_libCCECMock, getPhysicalAddress(::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](uint32_t* physAddress) {
                *physAddress = (uint32_t)0x12345678;
            }));

    ON_CALL(*p_messageEncoderMock, encode(::testing::Matcher<const DataBlock&>(::testing::_)))
        .WillByDefault(::testing::ReturnRef(CECFrame::getInstance()));
    ON_CALL(*p_messageEncoderMock, encode(::testing::Matcher<const UserControlPressed&>(::testing::_)))
        .WillByDefault(::testing::ReturnRef(CECFrame::getInstance()));

    ON_CALL(*p_iarmBusImplMock, IARM_Bus_RegisterEventHandler(::testing::_, ::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const char* ownerName, IARM_EventId_t eventId, IARM_EventHandler_t handler) {
                if ((string(IARM_BUS_DSMGR_NAME) == string(ownerName)) && (eventId == IARM_BUS_DSMGR_EVENT_HDMI_IN_HOTPLUG)) {
                    EXPECT_TRUE(handler != nullptr);
                    dsHdmiEventHandler = handler;
                }
                return IARM_RESULT_SUCCESS;
            }));

    ON_CALL(*p_connectionMock, addFrameListener(::testing::_))
        .WillByDefault([this](FrameListener* listener) {
            printf("[TEST] addFrameListener called with address: %p\n", static_cast<void*>(listener));
            this->listeners.push_back(listener);
        });

    ON_CALL(*p_connectionMock, open())
        .WillByDefault(::testing::Return());

    EXPECT_CALL(*p_hdmiInputImplMock, getNumberOfInputs())
        .WillRepeatedly(::testing::Return(3));

    ON_CALL(*p_hdmiInputImplMock, isPortConnected(::testing::_))
        .WillByDefault(::testing::Invoke(
            [](int8_t port) {
                return port == 1 ? true : false;
            }));

    EXPECT_CALL(*p_hdmiInputImplMock, getHDMIARCPortId(::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [](int& portId) -> dsError_t {
                portId = 1;
                return static_cast<dsError_t>(0);
            }));

    /* Activate plugin in constructor */
    status = ActivateService("org.rdk.PowerManager");
    EXPECT_EQ(Core::ERROR_NONE, status);

    status = ActivateService("org.rdk.HdmiCecSink");
    EXPECT_EQ(Core::ERROR_NONE, status);
}

HdmiCecSink_L2Test_STANDBY::~HdmiCecSink_L2Test_STANDBY()
{
    uint32_t status = Core::ERROR_GENERAL;

    ON_CALL(*p_connectionMock, close())
        .WillByDefault(::testing::Return());

    sleep(5);

    // Deactivate services in reverse order
    status = DeactivateService("org.rdk.HdmiCecSink");
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(*p_powerManagerHalMock, PLAT_TERM())
        .WillOnce(::testing::Return(PWRMGR_SUCCESS));

    EXPECT_CALL(*p_powerManagerHalMock, PLAT_DS_TERM())
        .WillOnce(::testing::Return(DEEPSLEEPMGR_SUCCESS));

    status = DeactivateService("org.rdk.PowerManager");
    EXPECT_EQ(Core::ERROR_NONE, status);

    removeFile("/opt/uimgr_settings.bin");
}

void HdmiCecSink_L2Test_STANDBY::SetUp()
{
    // Reset all event flags before each test to prevent race conditions from stale flags
    std::unique_lock<std::mutex> lock(m_mutex);
    m_event_signalled = HDMICECSINK_STATUS_INVALID;
}

void HdmiCecSink_L2Test_STANDBY::TearDown()
{
    // Hand the next test the CEC-enabled state this one inherited, not the one it needed.
    RestoreCecEnabledState();
}

void HdmiCecSink_L2Test::arcInitiationEvent(const JsonObject& message)
{
    TEST_LOG("arcInitiation event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("arcInitiation received: %s\n", str.c_str());

    /* Notify the requester thread. */
    m_event_signalled |= ARC_INITIATION_EVENT;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::arcTerminationEvent(const JsonObject& message)
{
    TEST_LOG("arcTermination event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("arcTermination received: %s\n", str.c_str());

    /* Notify the requester thread. */
    m_event_signalled |= ARC_TERMINATION_EVENT;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::onActiveSourceChange(const JsonObject& message)
{
    TEST_LOG("onActiveSourceChange event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("onActiveSourceChange received: %s\n", str.c_str());

    /* Notify the requester thread. */
    m_event_signalled |= ON_ACTIVE_SOURCE_CHANGE;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::onDeviceAdded(const JsonObject& message)
{
    TEST_LOG("onDeviceAdded event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("onDeviceAdded received: %s\n", str.c_str());

    /* Notify the requester thread. */
    m_event_signalled |= ON_DEVICE_ADDED;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::onDeviceInfoUpdated(const JsonObject& message)
{
    TEST_LOG("onDeviceInfoUpdated event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("onDeviceInfoUpdated received: %s\n", str.c_str());

    /* Notify the requester thread. */
    m_event_signalled |= ON_DEVICE_INFO_UPDATED;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::onDeviceRemoved(const JsonObject& message)
{
    TEST_LOG("onDeviceRemoved event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("onDeviceRemoved received: %s\n", str.c_str());

    /* Keep the payload, not just the event bit: an onDeviceRemoved that names the wrong device is
       a defect, and a test that only waits for the bit cannot see it. */
    m_jsonRemovedLogicalAddress = message.HasLabel("logicalAddress")
        ? static_cast<int>(message["logicalAddress"].Number()) : -1;
    /* One event per removed device, so keep the whole set - see the COM handler for why a single
       "last address" reading is not assertable on its own. */
    m_jsonRemovedLogicalAddresses.push_back(m_jsonRemovedLogicalAddress);

    /* Notify the requester thread. */
    m_event_signalled |= ON_DEVICE_REMOVED;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::onImageViewOnMsg(const JsonObject& message)
{
    TEST_LOG("onImageViewOnMsg event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("onImageViewOnMsg received: %s\n", str.c_str());

    /* Same reason as onDeviceRemoved: the initiator this event names is the thing worth asserting. */
    m_jsonImageViewOnLogicalAddress = message.HasLabel("logicalAddress")
        ? static_cast<int>(message["logicalAddress"].Number()) : -1;

    /* Notify the requester thread. */
    m_event_signalled |= ON_IMAGE_VIEW_ON;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::onInActiveSource(const JsonObject& message)
{
    TEST_LOG("onInActiveSource event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("onInActiveSource received: %s\n", str.c_str());

    /* Notify the requester thread. */
    m_event_signalled |= ON_INACTIVE_SOURCE;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::onTextViewOnMsg(const JsonObject& message)
{
    TEST_LOG("onTextViewOnMsg event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("onTextViewOnMsg received: %s\n", str.c_str());

    /* Notify the requester thread. */
    m_event_signalled |= ON_TEXT_VIEW_ON;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test_STANDBY::onWakeupFromStandby(const JsonObject& message)
{
    TEST_LOG("onWakeupFromStandby event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("onWakeupFromStandby received: %s\n", str.c_str());

    /* Notify the requester thread. */
    m_event_signalled |= ON_WAKEUP_FROM_STANDBY;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::reportAudioDeviceConnectedStatus(const JsonObject& message)
{
    TEST_LOG("reportAudioDeviceConnectedStatus event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("reportAudioDeviceConnectedStatus received: %s\n", str.c_str());

    /* Notify the requester thread. */
    m_event_signalled |= REPORT_AUDIO_DEVICE_CONNECTED;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::reportAudioStatusEvent(const JsonObject& message)
{
    TEST_LOG("reportAudioStatusEvent event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("reportAudioStatusEvent received: %s\n", str.c_str());

    /* Notify the requester thread. */
    m_event_signalled |= ON_REPORT_AUDIO_STATUS;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::reportFeatureAbortEvent(const JsonObject& message)
{
    TEST_LOG("reportFeatureAbortEvent event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("reportFeatureAbortEvent received: %s\n", str.c_str());

    /* Retain the payload so a test can assert WHICH device aborted WHICH opcode and WHY, rather
       than only that a <Feature Abort> notification arrived. The label spelling is the one
       JsonData::HdmiCecSink::ReportFeatureAbortEventParamsData registers. A missing label is
       recorded as -1 so an absent field fails an assertion instead of reading as a valid 0. */
    m_jsonFeatureAbortLogicalAddress = message.HasLabel("logicalAddress")
        ? static_cast<int>(message["logicalAddress"].Number())
        : -1;
    m_jsonFeatureAbortOpcode = message.HasLabel("opcode")
        ? static_cast<int>(message["opcode"].Number())
        : -1;
    m_jsonFeatureAbortReason = message.HasLabel("FeatureAbortReason")
        ? static_cast<int>(message["FeatureAbortReason"].Number())
        : -1;

    /* Notify the requester thread. */
    m_event_signalled |= REPORT_FEATURE_ABORT;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::reportCecEnabledEvent(const JsonObject& message)
{
    TEST_LOG("reportCecEnabledEvent event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("reportCecEnabledEvent received: %s\n", str.c_str());

    /* Notify the requester thread. */
    m_event_signalled |= REPORT_CEC_ENABLED;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::setSystemAudioModeEvent(const JsonObject& message)
{
    TEST_LOG("setSystemAudioModeEvent event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("setSystemAudioModeEvent received: %s\n", str.c_str());

    /* Notify the requester thread. */
    m_event_signalled |= ON_SET_SYSTEM_AUDIO_MODE;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::shortAudiodescriptorEvent(const JsonObject& message)
{
    TEST_LOG("shortAudiodescriptorEvent event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("shortAudiodescriptorEvent received: %s\n", str.c_str());

    /* Notify the requester thread. */
    m_event_signalled |= SHORT_AUDIO_DESCRIPTOR;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::standbyMessageReceived(const JsonObject& message)
{
    TEST_LOG("standbyMessageReceived event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("standbyMessageReceived received: %s\n", str.c_str());

    /* Notify the requester thread. */
    m_event_signalled |= STANDBY_MESSAGE_RECEIVED;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::reportAudioDevicePowerStatus(const JsonObject& message)
{
    TEST_LOG("reportAudioDevicePowerStatus event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("reportAudioDevicePowerStatus received: %s\n", str.c_str());

    /* Notify the requester thread. */
    m_event_signalled |= REPORT_AUDIO_DEVICE_POWER_STATUS;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::onKeyPressEvent(const JsonObject& message)
{
    TEST_LOG("onKeyPressEvent event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("onKeyPressEvent received: %s\n", str.c_str());

    m_logicalAddress = message["logicalAddress"].Number();
    m_keyCode = message["keyCode"].Number();

    m_event_signalled |= ON_KEY_PRESS_EVENT;
    m_condition_variable.notify_one();
}

void HdmiCecSink_L2Test::onKeyReleaseEvent(const JsonObject& message)
{
    TEST_LOG("onKeyReleaseEvent event triggered ***\n");
    std::unique_lock<std::mutex> lock(m_mutex);

    std::string str;
    message.ToString(str);

    TEST_LOG("onKeyReleaseEvent received: %s\n", str.c_str());

    m_logicalAddress = message["logicalAddress"].Number();

    m_event_signalled |= ON_KEY_RELEASE_EVENT;
    m_condition_variable.notify_one();
}

uint32_t HdmiCecSink_L2Test::WaitForRequestStatus(uint32_t timeout_ms, HdmiCecSinkL2test_async_events_t expected_status)
{
    std::unique_lock<std::mutex> lock(m_mutex);
    auto now = std::chrono::system_clock::now();
    std::chrono::milliseconds timeout(timeout_ms);
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    while (!(expected_status & m_event_signalled)) {
        if (m_condition_variable.wait_until(lock, now + timeout) == std::cv_status::timeout) {
            TEST_LOG("Timeout waiting for request status event");
            break;
        }
    }
    signalled = m_event_signalled;
    return signalled;
}

uint32_t HdmiCecSink_L2Test_STANDBY::WaitForRequestStatus(uint32_t timeout_ms, HdmiCecSinkL2test_async_events_t expected_status)
{
    std::unique_lock<std::mutex> lock(m_mutex);
    auto now = std::chrono::system_clock::now();
    std::chrono::milliseconds timeout(timeout_ms);
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    while (!(expected_status & m_event_signalled)) {
        if (m_condition_variable.wait_until(lock, now + timeout) == std::cv_status::timeout) {
            TEST_LOG("Timeout waiting for request status event");
            break;
        }
    }
    signalled = m_event_signalled;
    return signalled;
}

MATCHER_P(MatchRequest, data, "")
{
    bool match = true;
    std::string expected;
    std::string actual;

    data.ToString(expected);
    arg.ToString(actual);
    TEST_LOG(" rec = %s, arg = %s", expected.c_str(), actual.c_str());
    EXPECT_STREQ(expected.c_str(), actual.c_str());

    return match;
}

uint32_t HdmiCecSink_L2Test::CreateHdmiCecSinkInterfaceObject()
{
    uint32_t return_value = Core::ERROR_GENERAL;

    TEST_LOG("Creating HdmiCecSink_Engine");
    HdmiCecSink_Engine = Core::ProxyType<RPC::InvokeServerType<1, 0, 4>>::Create();
    HdmiCecSink_Client = Core::ProxyType<RPC::CommunicatorClient>::Create(Core::NodeId("/tmp/communicator"), Core::ProxyType<Core::IIPCServer>(HdmiCecSink_Engine));

    TEST_LOG("Creating HdmiCecSink_Engine Announcements");
#if ((THUNDER_VERSION == 2) || ((THUNDER_VERSION == 4) && (THUNDER_VERSION_MINOR == 2)))
    HdmiCecSink_Engine->Announcements(mHdmiCecSink_Client->Announcement());
#endif
    if (!HdmiCecSink_Client.IsValid()) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        m_controller_cecSink = HdmiCecSink_Client->Open<PluginHost::IShell>(_T("org.rdk.HdmiCecSink"), ~0, 3000);
        if (m_controller_cecSink) {
            m_cecSinkPlugin = m_controller_cecSink->QueryInterface<Exchange::IHdmiCecSink>();
            return_value = Core::ERROR_NONE;
        }
    }
    return return_value;
}

// Test cases to validate Set and Get OSDName COMRPC
TEST_F(HdmiCecSink_L2Test, Set_And_Get_OSDName_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;
                string name = "TEST", osdname;
                bool success;
                status = m_cecSinkPlugin->SetOSDName(name, result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                status = m_cecSinkPlugin->GetOSDName(osdname, success);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(success);
                EXPECT_EQ(osdname, "TEST");

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate Set and Get Enabled COMRPC
TEST_F(HdmiCecSink_L2Test, Set_And_Get_Enabled_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;
                bool success, enabled = false, response;
                status = m_cecSinkPlugin->SetEnabled(enabled, result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                status = m_cecSinkPlugin->GetEnabled(response, success);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(success);
                EXPECT_FALSE(response);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate Set and Get VendorId COMRPC
TEST_F(HdmiCecSink_L2Test, Set_And_Get_VendorId_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;
                std::string vendorId = "0xAABBCC", getVendorId;
                bool success;

                status = m_cecSinkPlugin->SetVendorId(vendorId, result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                status = m_cecSinkPlugin->GetVendorId(getVendorId, success);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(success);
                EXPECT_EQ(getVendorId, "aabbcc");

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate GetAudioDeviceConnectedStatus COMRPC
TEST_F(HdmiCecSink_L2Test, GetAudioDeviceConnectedStatus_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                bool connected, success;

                status = m_cecSinkPlugin->GetAudioDeviceConnectedStatus(connected, success);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_FALSE(connected);
                EXPECT_TRUE(success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate PrintDeviceList COMRPC
TEST_F(HdmiCecSink_L2Test, PrintDeviceList_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                bool printed, success;

                status = m_cecSinkPlugin->PrintDeviceList(printed, success);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(printed);
                EXPECT_TRUE(success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate RequestActiveSource COMRPC
TEST_F(HdmiCecSink_L2Test, RequestActiveSource_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;

                status = m_cecSinkPlugin->RequestActiveSource(result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate RequestShortAudioDescriptor COMRPC
TEST_F(HdmiCecSink_L2Test, RequestShortAudioDescriptor_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;

                status = m_cecSinkPlugin->RequestShortAudioDescriptor(result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate SendAudioDevicePowerOnMessage COMRPC
TEST_F(HdmiCecSink_L2Test, SendAudioDevicePowerOnMessage_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;

                status = m_cecSinkPlugin->SendAudioDevicePowerOnMessage(result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate SendGetAudioStatusMessage COMRPC
TEST_F(HdmiCecSink_L2Test, SendGetAudioStatusMessage_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;

                status = m_cecSinkPlugin->SendGetAudioStatusMessage(result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate SendKeyPressEvent COMRPC
TEST_F(HdmiCecSink_L2Test, SendKeyPressEvent_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;
                uint32_t logicaladdr = 0x1, keycode = 0x41;

                status = m_cecSinkPlugin->SendKeyPressEvent(logicaladdr, keycode, result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate SendUserControlPressed COMRPC
TEST_F(HdmiCecSink_L2Test, SendUserControlPressed_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;
                uint32_t logicaladdr = 0x1, keycode = 0x41;

                status = m_cecSinkPlugin->SendUserControlPressed(logicaladdr, keycode, result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate SendUserControlReleased COMRPC
TEST_F(HdmiCecSink_L2Test, SendUserControlReleased_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;
                uint32_t logicaladdr = 0x1;

                status = m_cecSinkPlugin->SendUserControlReleased(logicaladdr, result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate SendStandbyMessage COMRPC
TEST_F(HdmiCecSink_L2Test, SendStandbyMessage_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;

                status = m_cecSinkPlugin->SendStandbyMessage(result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate SetActivePath COMRPC
TEST_F(HdmiCecSink_L2Test, SetActivePath_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;
                string activepath = "2.0.0.0";

                status = m_cecSinkPlugin->SetActivePath(activepath, result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate SetActiveSource COMRPC
TEST_F(HdmiCecSink_L2Test, SetActiveSource_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;

                status = m_cecSinkPlugin->SetActiveSource(result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate SetMenuLanguage COMRPC
TEST_F(HdmiCecSink_L2Test, SetMenuLanguage_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;
                string lang = "eng";

                EXPECT_CALL(*p_connectionMock, sendTo(::testing::_, ::testing::_, ::testing::_))
                    .WillRepeatedly(::testing::Invoke(
                        [&](const LogicalAddress& to, const CECFrame& frame, int timeout) {
                            EXPECT_LE(to.toInt(), LogicalAddress::BROADCAST);
                            EXPECT_GT(timeout, 0);
                        }));

                status = m_cecSinkPlugin->SetMenuLanguage(lang, result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate SetRoutingChange COMRPC
TEST_F(HdmiCecSink_L2Test, SetRoutingChange_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;
                string oldport = "HDMI0", newport = "HDMI1";

                std::this_thread::sleep_for(std::chrono::seconds(30));

                status = m_cecSinkPlugin->SetRoutingChange(oldport, newport, result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate SetupARCRouting COMRPC
TEST_F(HdmiCecSink_L2Test, SetupARCRouting_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;
                bool enabled = true;

                EXPECT_CALL(*p_connectionMock, sendTo(testing::_, testing::_, testing::_)).Times(testing::AtLeast(1));

                status = m_cecSinkPlugin->SetupARCRouting(enabled, result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate SetLatencyInfo COMRPC
TEST_F(HdmiCecSink_L2Test, SetLatencyInfo_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;
                string videolatency = "2", lowLatencyMode = "1", audioOutputCompensated = "1", audioOutputDelay = "20";

                EXPECT_CALL(*p_connectionMock, sendTo(testing::_, testing::_, testing::_))
                    .Times(testing::AtLeast(1));

                status = m_cecSinkPlugin->SetLatencyInfo(videolatency, lowLatencyMode, audioOutputCompensated, audioOutputDelay, result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate RequestAudioDevicePowerStatus COMRPC
TEST_F(HdmiCecSink_L2Test, RequestAudioDevicePowerStatus_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                Core::hresult status = Core::ERROR_GENERAL;
                HdmiCecSinkSuccess result;

                status = m_cecSinkPlugin->RequestAudioDevicePowerStatus(result);
                EXPECT_EQ(status, Core::ERROR_NONE);
                if (status != Core::ERROR_NONE) {
                    std::string errorMsg = "COM-RPC returned error " + std::to_string(status) + " (" + std::string(Core::ErrorToString(status)) + ")";
                    TEST_LOG("Err: %s", errorMsg.c_str());
                }
                EXPECT_TRUE(result.success);

                m_cecSinkPlugin->Release();
            } else {
                TEST_LOG("m_cecSinkPlugin is NULL");
            }
            m_controller_cecSink->Release();
        } else {
            TEST_LOG("m_controller_cecSink is NULL");
        }
    }
}

// Test cases to validate GetActiveSource COMRPC
TEST_F(HdmiCecSink_L2Test, GetActiveSource_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                // Call GetActiveSource
                bool available;
                uint8_t logicalAddress;
                string physicalAddress, deviceType, cecVersion, osdname, vendID, powerStatus, port;
                bool success;

                auto result = m_cecSinkPlugin->GetActiveSource(available, logicalAddress,
                    physicalAddress, deviceType, cecVersion, osdname, vendID,
                    powerStatus, port, success);

                // Verify results
                EXPECT_EQ(result, Core::ERROR_NONE);
                EXPECT_TRUE(success);

                m_cecSinkPlugin->Release();
            }
            m_controller_cecSink->Release();
        }
    }
}

// Test cases to validate GetActiveRoute COMRPC
TEST_F(HdmiCecSink_L2Test, GetActiveRoute_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                // Call GetActiveRoute
                bool available, success;
                uint8_t length;
                IHdmiCecSinkActivePathIterator* list;
                string Activeroute;

                auto result = m_cecSinkPlugin->GetActiveRoute(available, length, list, Activeroute, success);

                // Verify results
                EXPECT_EQ(result, Core::ERROR_NONE);
                EXPECT_TRUE(success);
                EXPECT_FALSE(available);

                m_cecSinkPlugin->Release();
            }
            m_controller_cecSink->Release();
        }
    }
}

// Test cases to validate GetDeviceList COMRPC
TEST_F(HdmiCecSink_L2Test, GetDeviceList_COMRPC)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {

                // Call GetDeviceList
                bool success;
                uint32_t numberofdevices;
                IHdmiCecSinkDeviceListIterator* devicelist;

                auto result = m_cecSinkPlugin->GetDeviceList(numberofdevices, devicelist, success);

                // Verify results
                EXPECT_EQ(result, Core::ERROR_NONE);
                EXPECT_TRUE(success);

                m_cecSinkPlugin->Release();
            }
            m_controller_cecSink->Release();
        }
    }
}

// Test cases to validate Hdmihotplug COMRPC
TEST_F(HdmiCecSink_L2Test, Hdmihotplug_COMRPC_PlugIn_and_PlugOut)
{
    if (CreateHdmiCecSinkInterfaceObject() != Core::ERROR_NONE) {
        TEST_LOG("Invalid HdmiCecSink_Client");
    } else {
        EXPECT_TRUE(m_controller_cecSink != nullptr);
        if (m_controller_cecSink) {
            EXPECT_TRUE(m_cecSinkPlugin != nullptr);
            if (m_cecSinkPlugin) {
                ASSERT_NE(g_registeredHdmiInListener, nullptr);
                g_registeredHdmiInListener->OnHdmiInEventHotPlug(dsHDMI_IN_PORT_1, true);
                std::this_thread::sleep_for(std::chrono::seconds(2));
                g_registeredHdmiInListener->OnHdmiInEventHotPlug(dsHDMI_IN_PORT_1, false);
                m_cecSinkPlugin->Release();
            }
            m_controller_cecSink->Release();
        }
    }
}

// Test cases to validate Set and Get OSDName using JSONRPC
TEST_F(HdmiCecSink_L2Test, Set_And_Get_OSDName_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    // Test SetOSDName
    params["name"] = "TEST";
    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "setOSDName", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    // Verify with GetOSDName
    params.Clear();
    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "getOSDName", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("name"));
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
    EXPECT_STREQ("TEST", result["name"].String().c_str());
}

// Test cases to validate GetVendorId using JSONRPC
TEST_F(HdmiCecSink_L2Test, GetAudioDeviceConnectedStatus_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "getAudioDeviceConnectedStatus", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("connected"));
    EXPECT_FALSE(result["connected"].Boolean());
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
}

// Test cases to validate PrintDeviceList using JSONRPC
TEST_F(HdmiCecSink_L2Test, PrintDeviceList_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "printDeviceList", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("printed"));
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
    EXPECT_TRUE(result["printed"].Boolean());
}

// Test cases to validate PrintDeviceList using JSONRPC
TEST_F(HdmiCecSink_L2Test, RequestActiveSource_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    std::string message;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result, expected_status;

    /* Register for onDeviceAdded event. */
    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onDeviceAdded"),
        &AsyncHandlerMock_HdmiCecSink::onDeviceAdded,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    message = "{\"logicalAddress\":1}";
    expected_status.FromString(message);
    EXPECT_CALL(async_handler, onDeviceAdded(testing::_))
        .WillRepeatedly(Invoke(this, &HdmiCecSink_L2Test::onDeviceAdded));

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "requestActiveSource", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_DEVICE_ADDED);
    EXPECT_TRUE(signalled & ON_DEVICE_ADDED);

    /*Unregister for event*/
    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("onDeviceAdded"));
}

// Test cases to validate RequestShortAudioDescriptor using JSONRPC
TEST_F(HdmiCecSink_L2Test, RequestShortAudioDescriptor_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "requestShortAudioDescriptor", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
}

// Test cases to validate SendAudioDevicePowerOnMessage using JSONRPC
TEST_F(HdmiCecSink_L2Test, SendKeyPressEvent_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    params["logicalAddress"] = 4;
    params["keyCode"] = 65;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 0;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 1;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 2;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 3;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 4;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 9;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 13;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 32;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 33;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 34;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 35;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 36;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 37;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 38;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 39;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 40;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 41;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 66;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 67;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 101;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 102;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 108;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 109;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendKeyPressEvent", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
}

// Test cases to validate SendUserControlPressed using JSONRPC
TEST_F(HdmiCecSink_L2Test, SendUserControlPressed_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    params["logicalAddress"] = 4;
    params["keyCode"] = 65;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 0;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 1;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 2;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 3;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 4;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 9;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 13;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 32;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 33;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 34;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 35;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 36;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 37;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 38;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 39;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 40;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 41;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 66;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 67;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 101;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 102;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 108;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    params.Clear();
    params["logicalAddress"] = 4;
    params["keyCode"] = 109;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlPressed", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
}

// Test cases to validate SendUserControlReleased using JSONRPC
TEST_F(HdmiCecSink_L2Test, SendUserControlReleased_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    params["logicalAddress"] = 4;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendUserControlReleased", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
}

// Test cases to validate SetActivePath using JSONRPC
TEST_F(HdmiCecSink_L2Test, SetActivePath_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    params["activePath"] = "2.0.0.0";

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "setActivePath", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
}

// Test cases to validate SetActiveSource using JSONRPC
TEST_F(HdmiCecSink_L2Test, SetActiveSource_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "setActiveSource", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
}

// Test cases to validate SetActiveSource using JSONRPC
TEST_F(HdmiCecSink_L2Test, SetMenuLanguage_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    params["language"] = "chi";

    EXPECT_CALL(*p_connectionMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](const LogicalAddress& to, const CECFrame& frame, int timeout) {
                EXPECT_LE(to.toInt(), LogicalAddress::BROADCAST);
                EXPECT_GT(timeout, 0);
            }));

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "setMenuLanguage", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
}

// Test cases to validate SetRoutingChange using JSONRPC
TEST_F(HdmiCecSink_L2Test, SetRoutingChange_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    params["oldPort"] = "HDMI0";
    params["newPort"] = "TV";

    std::this_thread::sleep_for(std::chrono::seconds(30));

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "setRoutingChange", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
}

// Test cases to validate SetupARCRouting using JSONRPC
TEST_F(HdmiCecSink_L2Test, SetupARCRouting_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    params["enabled"] = true;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "setupARCRouting", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
}

// Test cases to validate SetLatencyInfo using JSONRPC
TEST_F(HdmiCecSink_L2Test, SetLatencyInfo_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    params["videoLatency"] = "2";
    params["lowLatencyMode"] = "1";
    params["audioOutputCompensated"] = "1";
    params["audioOutputDelay"] = "20";

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "setLatencyInfo", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
}

// Test cases to validate RequestAudioDevicePowerStatus using JSONRPC
TEST_F(HdmiCecSink_L2Test, RequestAudioDevicePowerStatus_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "requestAudioDevicePowerStatus", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
}

// Test cases to validate GetActiveSource using JSONRPC
TEST_F(HdmiCecSink_L2Test, GetActiveSource_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "getActiveSource", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result["success"].Boolean());
}

// Test cases to validate GetActiveRoute using JSONRPC
TEST_F(HdmiCecSink_L2Test, GetActiveRoute_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "getActiveRoute", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result["success"].Boolean());
}

// Test cases to validate GetDeviceList using JSONRPC
TEST_F(HdmiCecSink_L2Test, GetDeviceList_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "getDeviceList", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result["success"].Boolean());
}

// Test cases to validate SetVendorId and GetVendorId using JSONRPC
TEST_F(HdmiCecSink_L2Test, Set_And_Get_VendorId_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    // Test SetVendorId
    params["vendorid"] = "0xAABBCC";
    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "setVendorId", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    // Verify with GetVendorId
    params.Clear();
    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "getVendorId", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("vendorid"));
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
    EXPECT_STREQ("aabbcc", result["vendorid"].String().c_str());
}

// Test cases to validate SetEnabled and GetEnabled using JSONRPC
TEST_F(HdmiCecSink_L2Test, Set_And_Get_Enabled_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result, expected_status;
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    std::string message;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    /* Register for reportCecEnabledEvent event. */
    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("reportCecEnabledEvent"),
        &AsyncHandlerMock_HdmiCecSink::reportCecEnabledEvent,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    message = "{\"cecEnable\":false}";
    expected_status.FromString(message);
    EXPECT_CALL(async_handler, reportCecEnabledEvent(testing::_))
        .WillRepeatedly(Invoke(this, &HdmiCecSink_L2Test::reportCecEnabledEvent));

    // Test SetEnabled
    params["enabled"] = false;
    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "setEnabled", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    // Verify with GetEnabled
    params.Clear();
    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "getEnabled", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("enabled"));
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
    EXPECT_FALSE(result["enabled"].Boolean());

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, REPORT_CEC_ENABLED);
    EXPECT_TRUE(signalled & REPORT_CEC_ENABLED);

    /*Unregister for event*/
    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("reportCecEnabledEvent"));
}

// Test cases to validate SendAudioDevicePowerOnMessage using JSONRPC
TEST_F(HdmiCecSink_L2Test, SendAudioDevicePowerOnMessage_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    EXPECT_CALL(*p_connectionMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](const LogicalAddress& to, const CECFrame& frame, int timeout) {
                EXPECT_GT(timeout, 0);
            }));

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendAudioDevicePowerOnMessage", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
}

// Test cases to validate SendGetAudioStatusMessage using JSONRPC
TEST_F(HdmiCecSink_L2Test, SendGetAudioStatusMessage_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    EXPECT_CALL(*p_connectionMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](const LogicalAddress& to, const CECFrame& frame, int timeout) {
                EXPECT_GT(timeout, 0);
            }));

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendGetAudioStatusMessage", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
}

// Test cases to validate SendStandbyMessage using JSONRPC
TEST_F(HdmiCecSink_L2Test, SendStandbyMessage_JSONRPC)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    JsonObject params, result;

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "sendStandbyMessage", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());
}

// Inject CEC frames and verify onActiveSourceChange events
TEST_F(HdmiCecSink_L2Test, InjectActiveSourceFrameAndVerifyEvent)
{
    // Set up the JSON-RPC client and mock event handler
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    // Subscribe to the 'onActiveSourceChange' event and set an expectation
    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onActiveSourceChange"),
        &AsyncHandlerMock_HdmiCecSink::onActiveSourceChange,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    // We expect this event to be fired with logicalAddress 1 and physicalAddress "1.0.0.0"
    EXPECT_CALL(async_handler, onActiveSourceChange(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::onActiveSourceChange));

    // Ensure the plugin has registered its listener
    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Create the fake CEC frame for <Active Source>
    // Header: From Playback Device 1 (LA=4) to Broadcast (LA=15)
    // Opcode: 0x82 (Active Source)
    // Operands: 0x10, 0x00 (Physical Address 1.0.0.0)
    uint8_t buffer[] = { 0x4F, 0x82, 0x10, 0x00 };
    CECFrame activeSourceFrame(buffer, sizeof(buffer));

    // Inject the frame by calling notify() on the captured listener(s)
    for (auto* listener : listeners) {
        if (listener) {
            // This call simulates the ccec library delivering a frame to the plugin
            listener->notify(activeSourceFrame);
        }
    }

    // Wait for the event to be signalled by the mock handler
    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_ACTIVE_SOURCE_CHANGE);
    EXPECT_TRUE(signalled & ON_ACTIVE_SOURCE_CHANGE);

    // Clean up the subscription
    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("onActiveSourceChange"));
}

// Inject InActiveSource frames and verify onInActiveSource events
TEST_F(HdmiCecSink_L2Test, InjectInactiveSourceFramesAndVerifyEvents)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onInActiveSource"),
        &AsyncHandlerMock_HdmiCecSink::onInActiveSource,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, onInActiveSource(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::onInActiveSource));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Inject <Inactive Source>
    uint8_t inactiveSource[] = { 0x40, 0x9D, 0x10, 0x00 };
    CECFrame inactiveSourceFrame(inactiveSource, sizeof(inactiveSource));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(inactiveSourceFrame);
    }

    // Wait for both events
    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_INACTIVE_SOURCE);
    EXPECT_TRUE(signalled & ON_INACTIVE_SOURCE);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("onInActiveSource"));
}

// InActiveSource Broadcast frame should be ignored
TEST_F(HdmiCecSink_L2Test, InjectInactiveSourceBroadcastIgnoreCase)
{
    // Inject <Inactive Source>
    uint8_t inactiveSource[] = { 0x4F, 0x9D, 0x10, 0x00 };
    CECFrame inactiveSourceFrame(inactiveSource, sizeof(inactiveSource));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(inactiveSourceFrame);
    }
}

// Inject ImageViewOn frame and verify onImageViewOnMsg event.
//
// REMEDIATED (was DISABLED_InjectImageViewOnFrameAndVerifyEvent, "disabled due to implementation
// issue"). There is no implementation issue: updateImageViewOn() fans out onImageViewOnMsg for a
// directed frame from a registered initiator, and it ADDITIONALLY raises onWakeupFromStandby when
// the initiating device is already present and the panel reports standby. The original test used a
// StrictMock and subscribed to onImageViewOnMsg only, so that companion notification - and any
// device-discovery event the poll thread happened to emit while the frame was in flight - had no
// expectation to land on. Two test-side changes make the case deterministic without weakening what
// it asserts:
//   * WillRepeatedly instead of WillOnce, because the plugin is free to notify more than once
//     (the poll thread can re-announce the same initiator) and the assertion of interest is that
//     the event arrives at all;
//   * a wait on the event bit through the mask, which the handler now initialises properly, so the
//     result does not depend on a stale mask value.
// The frame, the subscription and the assertion are otherwise the original ones.
//
// The measurement that justified re-enabling it, run in place at this position in the file with
// GTEST_ALSO_RUN_DISABLED_TESTS=1 and the filter
// HdmiCecSink_L2Test.DISABLED_InjectImageViewOnFrameAndVerifyEvent:
//     [ RUN      ] HdmiCecSink_L2Test.DISABLED_InjectImageViewOnFrameAndVerifyEvent
//     [       OK ] HdmiCecSink_L2Test.DISABLED_InjectImageViewOnFrameAndVerifyEvent (6377 ms)
//     [  PASSED  ] 1 test.
// So COVERAGE_GAPS.md's reading of the DISABLED_ prefix as "itself evidence the defect is real" does
// not hold: the case passes on the production code as it stands. The two changes above are hardening
// against the poll thread's timing, not repairs of a production defect, and the full suite is re-run
// afterwards to confirm the case also passes among its neighbours rather than only in isolation.
//
// On the ImageViewOn/wake-from-standby interaction specifically: the sibling notification is real but
// cannot reach this mock. HdmiCecSinkImplementation::updateImageViewOn (cpp:1871-1898) raises
// OnWakeupFromStandby only when the initiator is present AND
// deviceList[m_logicalAddressAllocated].m_powerStatus == PowerStatus::STANDBY; this fixture is not
// the standby one, and the mock below carries no onWakeupFromStandby expectation for such a
// notification to land on. The standby behaviour is covered separately by the
// HdmiCecSink_L2Test_STANDBY fixture.
//
// The case lower down in this file that used to be a byte-for-byte copy of this one is kept, but
// rewritten to a DIFFERENT initiator (Playback Device 2 at logical address 8) and asserted on the
// reported payload, so it is a distinct scenario rather than a duplicate of an enabled test.
TEST_F(HdmiCecSink_L2Test, InjectImageViewOnFrameAndVerifyEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    // Checked BEFORE subscribing: this is a fatal assertion, so a missing listener must not abort
    // the body while a subscription is outstanding.
    ASSERT_FALSE(listeners.empty()) << "No FrameListener was captured. The plugin might not have initialized correctly.";

    ResetJsonEventState();

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onImageViewOnMsg"),
        &AsyncHandlerMock_HdmiCecSink::onImageViewOnMsg,
        &async_handler);
    ASSERT_EQ(Core::ERROR_NONE, status);
    JsonRpcSubscription subscription(jsonrpc, _T("onImageViewOnMsg"));

    EXPECT_CALL(async_handler, onImageViewOnMsg(::testing::_))
        .WillRepeatedly(Invoke(this, &HdmiCecSink_L2Test::onImageViewOnMsg));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Header: From Playback Device (4) to TV (0), Opcode: 0x04 (Image View On)
    uint8_t buffer[] = { 0x40, 0x04 };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_IMAGE_VIEW_ON);
    EXPECT_TRUE(signalled & ON_IMAGE_VIEW_ON);
    EXPECT_EQ(4, JsonImageViewOnLogicalAddress())
        << "onImageViewOnMsg named the wrong initiator";
}

// Inject TextViewOn frame and verify onTextViewOnMsg event
TEST_F(HdmiCecSink_L2Test, InjectTextViewOnFrameAndVerifyEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onTextViewOnMsg"),
        &AsyncHandlerMock_HdmiCecSink::onTextViewOnMsg,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, onTextViewOnMsg(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::onTextViewOnMsg));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Header: From TV (0) to Playback Device 1 (4), Opcode: 0x0D (Text View On)
    uint8_t buffer[] = { 0x40, 0x0D };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_TEXT_VIEW_ON);
    EXPECT_TRUE(signalled & ON_TEXT_VIEW_ON);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("onTextViewOnMsg"));
}

// TextViewOn Broadcast frame should be ignored
TEST_F(HdmiCecSink_L2Test, InjectTextViewOnFrameBroadcastIgnoreCase)
{
    uint8_t buffer[] = { 0x4F, 0x0D };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }
}

// Inject DeviceAdded frame and verify onDeviceAdded event
TEST_F(HdmiCecSink_L2Test, InjectDeviceAddedFrameAndVerifyEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onDeviceAdded"),
        &AsyncHandlerMock_HdmiCecSink::onDeviceAdded,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, onDeviceAdded(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::onDeviceAdded));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Report Physical Address - announces a new device
    // Header: From device 4 to broadcast, Opcode: 0x84 (Report Physical Address),
    // Physical Address: 0x20, 0x00, Device Type: 0x04 (Playback Device)
    uint8_t buffer[] = { 0x4F, 0x84, 0x20, 0x00, 0x04 };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_DEVICE_ADDED);
    EXPECT_TRUE(signalled & ON_DEVICE_ADDED);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("onDeviceAdded"));
}

// Inject DeviceAdded frame and verify reportAudioDeviceConnectedStatus event
TEST_F(HdmiCecSink_L2Test, InjectDeviceAddedFrameAndVerifyEvent_ReportAudioDeviceConnectedStatus)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("reportAudioDeviceConnectedStatus"),
        &AsyncHandlerMock_HdmiCecSink::reportAudioDeviceConnectedStatus,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, reportAudioDeviceConnectedStatus(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::reportAudioDeviceConnectedStatus));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Report Physical Address - announces a new device
    // Header: From device 5 to broadcast, Opcode: 0x84 (Report Physical Address),
    // Physical Address: 0x20, 0x00, Device Type: 0x04 (Playback Device)
    uint8_t buffer[] = { 0x5F, 0x84, 0x20, 0x00, 0x04 };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, REPORT_AUDIO_DEVICE_CONNECTED);
    EXPECT_TRUE(signalled & REPORT_AUDIO_DEVICE_CONNECTED);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("reportAudioDeviceConnectedStatus"));
}

// Report Audio Status
TEST_F(HdmiCecSink_L2Test, InjectReportAudioStatusAndVerifyEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("reportAudioStatusEvent"),
        &AsyncHandlerMock_HdmiCecSink::reportAudioStatusEvent,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, reportAudioStatusEvent(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::reportAudioStatusEvent));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Report Audio Status from Audio System (5) to TV (0)
    // Header: 0x50, Opcode: 0x7A (Report Audio Status), Status: 0x50 (Volume 80, not muted)
    uint8_t buffer[] = { 0x50, 0x7A, 0x50 };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_REPORT_AUDIO_STATUS);
    EXPECT_TRUE(signalled & ON_REPORT_AUDIO_STATUS);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("reportAudioStatusEvent"));
}

// Report Audio Status Broadcast frame should be ignored
TEST_F(HdmiCecSink_L2Test, InjectReportAudioStatusAndVerifyEventBroadcastIgnoreTest)
{
    // Header: 0x50, Opcode: 0x7A (Report Audio Status), Status: 0x50 (Volume 80, not muted)
    uint8_t buffer[] = { 0x5F, 0x7A, 0x50 };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }
}

// Feature Abort Broadcast frame should be ignored
TEST_F(HdmiCecSink_L2Test, InjectFeatureAbortFrameBroadcastIgnoreTest)
{
    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";
    // Feature Abort from device 4 to TV (0)
    // Header: 0x40, Opcode: 0x00 (Feature Abort), Rejected Opcode: 0x82, Reason: 0x04 (Refused)
    uint8_t buffer[] = { 0x4F, 0x00, 0x82, 0x04 };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }
}

/* Assert that a BROADCAST <Feature Abort> is dropped without reporting, on BOTH transports.
 *
 * The pre-existing sibling InjectFeatureAbortFrameBroadcastIgnoreTest injects the same frame but
 * asserts nothing at all - it only proves the injection does not throw. This adjacent test supplies
 * the missing observation: it subscribes to reportFeatureAbortEvent over JSON-RPC, registers a COM
 * notification, injects the broadcast frame, and then proves with a bounded wait that neither
 * transport delivered anything and that no payload field was written.
 *
 * That is the guard arm of HdmiCecSinkProcessor::process(FeatureAbort const&, Header const&):
 *   if (header.to.toInt() == LogicalAddress::BROADCAST) { ...; return; }
 *
 * WHY ONLY THE GUARD ARM IS TESTED HERE - the directed arm is BLOCKED at L2.
 * A DIRECTED <Feature Abort> ({0x40,0x00,0x82,0x04}) segfaults the plugin host. Measured, with the
 * backtrace taken from the host core dump:
 *     #0 AbortReason::toInt() const                                        <- SIGSEGV
 *     #1 HdmiCecSinkProcessor::process(FeatureAbort const&, Header const&)
 *     #2 MessageDecoder::decode  (entservices-testframework/Tests/mocks/HdmiCec.cpp:130)
 *     #3 HdmiCecSinkFrameListener::notify(CECFrame const&) const
 *     #4 <this test suite>::TestBody()
 * The cause is a defect in the mock CEC library, not in the plugin and not in this test:
 *   - HdmiCec.h declares `AbortReason* impl;` with no default member initialiser, and
 *     `AbortReason(int reason) : CECBytes((uint8_t)reason){ }` never assigns it;
 *   - `AbortReason::toInt()` then evaluates `if (impl && impl != this) return impl->toInt();`,
 *     a virtual call through an indeterminate pointer;
 *   - `FeatureAbort(const CECFrame&, int startPos)` builds its reason through exactly that int
 *     constructor, so every frame-decoded <Feature Abort> carries a wild `impl`.
 *     (The DEFAULT constructor is fine - HdmiCec.cpp:321 initialises impl(nullptr) - which is why
 *     the L1 tests, which build the operand themselves, can cover the directed path.)
 * Production reaches reportFeatureAbortEvent() only from the directed arm of this one function, and
 * no frame shape avoids that constructor, so the notification cannot be provoked from L2 at all.
 * Fixing it needs one line - `AbortReason* impl = nullptr;` - in entservices-testframework, which
 * AAP section 0.10.2 places out of scope for edits, and which every plugin's L2 suite shares.
 * Reported, deliberately not changed here.
 *
 * The directed arm is NOT left uncovered: the sink L1 suite asserts the full reported triple
 * (logical address, rejected opcode, abort reason) for all five abort reasons and all three
 * boundary values, because at L1 the operand is constructed in the test with impl set.
 */
TEST_F(HdmiCecSink_L2Test, InjectBroadcastFeatureAbortAndVerifyNoEventOnEitherTransport)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;
    uint32_t directSignalled = HDMICECSINK_STATUS_INVALID;

    // Long enough for an in-process fan-out to have landed if one were going to, short enough that
    // proving absence does not cost a full event timeout.
    const uint32_t kAbsenceTimeoutMs = 1500;

    // Listener availability is a FATAL precondition, so it is checked before anything is
    // subscribed, registered or acquired - see JsonRpcSubscription/SinkInterfaceScope.
    ASSERT_FALSE(listeners.empty()) << "No FrameListener was captured.";

    ResetJsonEventState();
    m_notificationHandler.ResetEvent();

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("reportFeatureAbortEvent"),
        &AsyncHandlerMock_HdmiCecSink::reportFeatureAbortEvent,
        &async_handler);
    ASSERT_EQ(Core::ERROR_NONE, status);
    JsonRpcSubscription subscription(jsonrpc, _T("reportFeatureAbortEvent"));

    ASSERT_EQ(Core::ERROR_NONE, CreateHdmiCecSinkInterfaceObject());
    ASSERT_NE(nullptr, m_controller_cecSink);
    ASSERT_NE(nullptr, m_cecSinkPlugin);
    SinkInterfaceScope interfaceScope(m_cecSinkPlugin, m_controller_cecSink, &m_notificationHandler);

    // Attach the notification only once the discovery sweep is quiet - see
    // WaitForDiscoveryToSettle for the unlocked production fan-out this avoids.
    ASSERT_TRUE(WaitForDiscoveryToSettle(m_cecSinkPlugin))
        << "CEC device discovery did not settle; attaching a notification now would race the "
           "unlocked notification fan-out in HdmiCecSinkImplementation.";
    ASSERT_EQ(Core::ERROR_NONE, m_cecSinkPlugin->Register(&m_notificationHandler));

    // No call is expected at all. The mock is a StrictMock, so an unexpected invocation is itself a
    // failure - the EXPECT_CALL below states the zero-cardinality explicitly so the intent is
    // readable rather than implied, and names the callback that would have recorded the payload.
    EXPECT_CALL(async_handler, reportFeatureAbortEvent(::testing::_))
        .Times(0);

    // <Feature Abort> from Playback Device 1 (4) addressed to BROADCAST (0xF).
    // Opcode 0x00 = <Feature Abort>, rejected feature 0x82 = <Active Source>, reason 4 = Refused.
    uint8_t buffer[] = { 0x4F, 0x00, 0x82, 0x04 };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    // JSON-RPC: nothing delivered, and no payload field written.
    signalled = WaitForRequestStatus(kAbsenceTimeoutMs, REPORT_FEATURE_ABORT);
    EXPECT_FALSE(signalled & REPORT_FEATURE_ABORT)
        << "A <Feature Abort> addressed to BROADCAST must not be reported over JSON-RPC.";
    EXPECT_EQ(-1, JsonFeatureAbortLogicalAddress());
    EXPECT_EQ(-1, JsonFeatureAbortOpcode());
    EXPECT_EQ(-1, JsonFeatureAbortReason());

    // COM-RPC: likewise nothing delivered to a handler that does override the method.
    directSignalled = m_notificationHandler.WaitForRequestStatus(kAbsenceTimeoutMs, REPORT_FEATURE_ABORT);
    EXPECT_FALSE(directSignalled & REPORT_FEATURE_ABORT)
        << "A <Feature Abort> addressed to BROADCAST must not be reported over COM-RPC.";
    EXPECT_EQ(-1, m_notificationHandler.GetFeatureAbortLogicalAddress());
    EXPECT_EQ(-1, m_notificationHandler.GetFeatureAbortOpcode());
    EXPECT_EQ(-1, m_notificationHandler.GetFeatureAbortReason());

    // Unsubscribe, Unregister and Release are owned by the scope guards above.
}

// Inject SetSystemAudioMode frame and verify setSystemAudioModeEvent event
TEST_F(HdmiCecSink_L2Test, InjectSetSystemAudioModeAndVerifyEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("setSystemAudioModeEvent"),
        &AsyncHandlerMock_HdmiCecSink::setSystemAudioModeEvent,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, setSystemAudioModeEvent(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::setSystemAudioModeEvent));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Set System Audio Mode from Audio System (5) to TV (0)
    // Header: 0x50, Opcode: 0x72 (Set System Audio Mode), Status: 0x01 (On)
    uint8_t buffer[] = { 0x50, 0x72, 0x01 };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_SET_SYSTEM_AUDIO_MODE);
    EXPECT_TRUE(signalled & ON_SET_SYSTEM_AUDIO_MODE);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("setSystemAudioModeEvent"));
}

// Inject CECVersion frame and verify onDeviceInfoUpdated event
TEST_F(HdmiCecSink_L2Test, InjectCECVersionAndVerifyOnDeviceInfoUpdated)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;
    uint32_t status = Core::ERROR_GENERAL;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onDeviceInfoUpdated"),
        &AsyncHandlerMock_HdmiCecSink::onDeviceInfoUpdated,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, onDeviceInfoUpdated(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::onDeviceInfoUpdated));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Simulate a CECVersion message from logical address 4 to us (0)
    uint8_t buffer[] = { 0x40, 0x9E, 0x05 }; // 0x05 = Version 1.4
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_DEVICE_INFO_UPDATED);
    EXPECT_TRUE(signalled & ON_DEVICE_INFO_UPDATED);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("onDeviceInfoUpdated"));
}

// RequestActiveSource (0x85)
TEST_F(HdmiCecSink_L2Test, InjectRequestActiveSourceFrame)
{
    uint8_t buffer[] = { 0x4F, 0x85 }; // From device 4 to broadcast
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// RequestActiveSource frame with direct message ingnored
TEST_F(HdmiCecSink_L2Test, InjectRequestActiveSourceFrameDirectMessageIgnoreTest)
{
    uint8_t buffer[] = { 0x40, 0x85 }; // From device 4 to TV
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// GetCECVersion (0x9F)
TEST_F(HdmiCecSink_L2Test, InjectGetCECVersionFrame)
{
    uint8_t buffer[] = { 0x40, 0x9F }; // From device 4 to TV (0)
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// GetCECVersion Broadcast frame should be ignored
TEST_F(HdmiCecSink_L2Test, InjectGetCECVersionFrameroadcastIgnoreTest)
{
    uint8_t buffer[] = { 0x4F, 0x9F }; // From device 4 to broadcast
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// GetCECVersion frame with exception in sendToAsync
TEST_F(HdmiCecSink_L2Test, InjectGetCECVersionFrameException)
{
    EXPECT_CALL(*p_connectionMock, sendToAsync(::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](const LogicalAddress& to, const CECFrame& frame) {
                throw Exception();
            }));

    uint8_t buffer[] = { 0x40, 0x9F }; // From device 4 to TV (0)
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// GiveOSDName (0x46)
TEST_F(HdmiCecSink_L2Test, InjectGiveOSDNameFrame)
{
    uint8_t buffer[] = { 0x40, 0x46 }; // From device 4 to TV (0)
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// GiveOSDName frame with exception in sendToAsync
TEST_F(HdmiCecSink_L2Test, InjectGiveOSDNameFrameException)
{
    uint8_t buffer[] = { 0x40, 0x46 }; // From device 4 to TV (0)

    EXPECT_CALL(*p_connectionMock, sendToAsync(::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](const LogicalAddress& to, const CECFrame& frame) {
                throw Exception();
            }));

    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// GiveOSDName Broadcast frame should be ignored
TEST_F(HdmiCecSink_L2Test, InjectGiveOSDNameFrameBroadcastIgnoreTest)
{
    uint8_t buffer[] = { 0x4F, 0x46 }; // From device 4 to broadcast
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// GivePhysicalAddress (0x83)
TEST_F(HdmiCecSink_L2Test, InjectGivePhysicalAddressFrame)
{
    uint8_t buffer[] = { 0x40, 0x83 }; // From device 4 to TV (0)
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// GivePhysicalAddress Broadcast frame should be ignored
TEST_F(HdmiCecSink_L2Test, InjectGivePhysicalAddressFrameException)
{
    EXPECT_CALL(*p_connectionMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillOnce(::testing::Invoke(
            [&](const LogicalAddress&, const CECFrame&, int) {
                throw std::runtime_error("Simulated sendTo failure");
            }))
        .WillRepeatedly(::testing::Return());

    uint8_t buffer[] = { 0x40, 0x83 }; // From device 4 to TV (0)
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// GiveDeviceVendorID (0x8C)
TEST_F(HdmiCecSink_L2Test, InjectGiveDeviceVendorIDFrame)
{
    uint8_t buffer[] = { 0x40, 0x8C }; // From device 4 to TV (0)
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// GiveDeviceVendorID Broadcast frame should be ignored
TEST_F(HdmiCecSink_L2Test, InjectGiveDeviceVendorIDFrameBroadcastIgnoreTest)
{
    uint8_t buffer[] = { 0x4F, 0x8C }; // From device 4 to broadcast
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// GiveDeviceVendorID frame with exception in sendToAsync
TEST_F(HdmiCecSink_L2Test, InjectGiveDeviceVendorIDFrameBroadcastException)
{
    uint8_t buffer[] = { 0x40, 0x8C }; // From device 4 to TV (0)

    EXPECT_CALL(*p_connectionMock, sendToAsync(::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](const LogicalAddress& to, const CECFrame& frame) {
                throw Exception();
            }));

    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// SetOSDString (0x64)
TEST_F(HdmiCecSink_L2Test, InjectSetOSDStringFrame)
{
    uint8_t buffer[] = { 0x40, 0x64, 0x41, 0x42, 0x43 }; // From device 4 to TV (0), string "ABC"
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// SetOSDName (0x47)
TEST_F(HdmiCecSink_L2Test, InjectSetOSDNameFrame)
{
    uint8_t buffer[] = { 0x40, 0x47, 'T', 'E', 'S', 'T' }; // From device 4 to TV (0), name "TEST"
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// SetOSDName Broadcast frame should be ignored
TEST_F(HdmiCecSink_L2Test, InjectSetOSDNameBroadcastIgnoreTest)
{
    uint8_t buffer[] = { 0x4F, 0x47, 'T', 'E', 'S', 'T' }; // From device 4 to broadcast, name "TEST"
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// RoutingChange (0x80)
TEST_F(HdmiCecSink_L2Test, InjectRoutingChangeFrame)
{
    uint8_t buffer[] = { 0x40, 0x80, 0x10, 0x00, 0x20, 0x00 }; // From device 4 to TV (0), old PA 1.0.0.0, new PA 2.0.0.0
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// RoutingInformation (0x81)
TEST_F(HdmiCecSink_L2Test, InjectRoutingInformationFrame)
{
    uint8_t buffer[] = { 0x40, 0x81, 0x20, 0x00 }; // From device 4 to TV (0), PA 2.0.0.0
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// SetStreamPath (0x86)
TEST_F(HdmiCecSink_L2Test, InjectSetStreamPathFrame)
{
    uint8_t buffer[] = { 0x4F, 0x86, 0x20, 0x00 }; // From device 4 to broadcast, PA 2.0.0.0
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// GetMenuLanguage (0x91)
TEST_F(HdmiCecSink_L2Test, InjectGetMenuLanguageFrame)
{
    uint8_t buffer[] = { 0x40, 0x91 }; // From device 4 to TV (0)
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// GetMenuLanguage Broadcast frame should be ignored
TEST_F(HdmiCecSink_L2Test, InjectGetMenuLanguageFrameBroadcastIgnoreTest)
{
    uint8_t buffer[] = { 0x4F, 0x91 }; // From device 4 to broadcast
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// GiveDevicePowerStatus (0x8F)
TEST_F(HdmiCecSink_L2Test, InjectGiveDevicePowerStatusFrame)
{
    uint8_t buffer[] = { 0x40, 0x8F }; // From device 4 to TV (0)
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// GiveDevicePowerStatus Broadcast frame should be ignored
TEST_F(HdmiCecSink_L2Test, InjectGiveDevicePowerStatusFrameBroadcastIgnoreTest)
{
    uint8_t buffer[] = { 0x4F, 0x8F }; // From device 4 to broadcast
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// GiveDevicePowerStatus frame with exception in sendTo
TEST_F(HdmiCecSink_L2Test, InjectGiveDevicePowerStatusFrameException)
{
    uint8_t buffer[] = { 0x40, 0x8F }; // From device 4 to TV (0)

    EXPECT_CALL(*p_connectionMock, sendTo(::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](const LogicalAddress& to, const CECFrame& frame) {
                throw Exception();
            }));

    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// InitiateArc (0xC0) TerminateArc (0xC5)
TEST_F(HdmiCecSink_L2Test, InjectInitiateAndTerminateArcFrameAndVerifyEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("arcInitiationEvent"),
        &AsyncHandlerMock_HdmiCecSink::arcInitiationEvent,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, arcInitiationEvent(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::arcInitiationEvent));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Inject Initiate ARC frame
    uint8_t initbuffer[] = { 0x50, 0xC0 }; // From Audio System (5) to TV (0)
    CECFrame initframe(initbuffer, sizeof(initbuffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(initframe);
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ARC_INITIATION_EVENT);
    EXPECT_TRUE(signalled & ARC_INITIATION_EVENT);

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("arcTerminationEvent"),
        &AsyncHandlerMock_HdmiCecSink::arcTerminationEvent,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, arcTerminationEvent(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::arcTerminationEvent));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Inject Terminate ARC frame
    uint8_t termbuffer[] = { 0x50, 0xC5 }; // From Audio System (5) to TV (0)
    CECFrame termframe(termbuffer, sizeof(termbuffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(termframe);
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ARC_TERMINATION_EVENT);
    EXPECT_TRUE(signalled & ARC_TERMINATION_EVENT);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("arcTerminationEvent"));

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("arcInitiationEvent"));
}

// Initiate & Terminate ARC frame
TEST_F(HdmiCecSink_L2Test, InjectInitiateArcFrameBroadcastIgnoreTest)
{
    uint8_t initbuffer[] = { 0x5F, 0xC0 }; // From Audio System (5) to TV (0)
    CECFrame initframe(initbuffer, sizeof(initbuffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(initframe);
    }

    uint8_t termbuffer[] = { 0x5F, 0xC5 }; // From Audio System (5) to TV (0)
    CECFrame termframe(termbuffer, sizeof(termbuffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(termframe);
    }
}

// GiveFeatures (0xA5)
TEST_F(HdmiCecSink_L2Test, InjectGiveFeaturesFrame)
{
    uint8_t buffer[] = { 0x40, 0xA5 }; // From device 4 to TV (0)
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// RequestCurrentLatency (0xA7)
TEST_F(HdmiCecSink_L2Test, InjectRequestCurrentLatencyFrame)
{
    uint8_t buffer[] = { 0x40, 0xA7, 0x10, 0x00 }; // From device 4 to TV (0), PA 1.0.0.0
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// ReportPhysicalAddress (0x84)
TEST_F(HdmiCecSink_L2Test, ReportPhysicalAddressBroadcastIgnoreCase)
{
    // Add a device on port 1 (logical address 4)
    uint8_t addBuffer[] = { 0x40, 0x84, 0x10, 0x00, 0x04 }; // From 4 to TV
    CECFrame addFrame(addBuffer, sizeof(addBuffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(addFrame);
    }
}

// Report Short Audio Descriptor (0xA3) and verify shortAudiodescriptorEvent event
TEST_F(HdmiCecSink_L2Test, InjectReportShortAudioDescriptorAndVerifyEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("shortAudiodescriptorEvent"),
        &AsyncHandlerMock_HdmiCecSink::shortAudiodescriptorEvent,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, shortAudiodescriptorEvent(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::shortAudiodescriptorEvent));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Report Short Audio Descriptor from Audio System (5) to TV (0)
    // Header: 0x50, Opcode: 0xA3 (Report Short Audio Descriptor)
    uint8_t buffer[] = { 0x50, 0xA3, 0x02, 0x0A }; // Example SAD bytes
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, SHORT_AUDIO_DESCRIPTOR);
    EXPECT_TRUE(signalled & SHORT_AUDIO_DESCRIPTOR);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("shortAudiodescriptorEvent"));
}

// Standby (0x36) and verify standbyMessageReceived event
TEST_F(HdmiCecSink_L2Test, InjectStandbyFrameAndVerifyEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("standbyMessageReceived"),
        &AsyncHandlerMock_HdmiCecSink::standbyMessageReceived,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, standbyMessageReceived(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::standbyMessageReceived));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Standby from device 4 to TV (0)
    uint8_t buffer[] = { 0x40, 0x36 };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, STANDBY_MESSAGE_RECEIVED);
    EXPECT_TRUE(signalled & STANDBY_MESSAGE_RECEIVED);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("standbyMessageReceived"));
}

// Report Power Status (0x90) and verify reportAudioDevicePowerStatus event
TEST_F(HdmiCecSink_L2Test, InjectReportPowerStatusAndVerifyEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;
    JsonObject params, result;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("reportAudioDevicePowerStatus"),
        &AsyncHandlerMock_HdmiCecSink::reportAudioDevicePowerStatus,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, reportAudioDevicePowerStatus(::testing::_))
        .Times(2)
        .WillRepeatedly(Invoke(this, &HdmiCecSink_L2Test::reportAudioDevicePowerStatus));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    status = InvokeServiceMethod("org.rdk.HdmiCecSink", "requestAudioDevicePowerStatus", params, result);
    EXPECT_EQ(Core::ERROR_NONE, status);
    EXPECT_TRUE(result.HasLabel("success"));
    EXPECT_TRUE(result["success"].Boolean());

    // First, inject OFF status
    uint8_t buffer_off[] = { 0x50, 0x90, 0x01 }; // 0x01 = Standby
    CECFrame frame_off(buffer_off, sizeof(buffer_off));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame_off);
    }

    // Then, inject ON status (should trigger the event)
    uint8_t buffer_on[] = { 0x50, 0x90, 0x00 }; // 0x00 = ON
    CECFrame frame_on(buffer_on, sizeof(buffer_on));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame_on);
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, REPORT_AUDIO_DEVICE_POWER_STATUS);
    EXPECT_TRUE(signalled & REPORT_AUDIO_DEVICE_POWER_STATUS);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("reportAudioDevicePowerStatus"));
}

// Report Power Status (0x90) Broadcast frame should be ignored
TEST_F(HdmiCecSink_L2Test, InjectReportPowerStatusBroadcastIgnoreTest)
{
    // Then, inject ON status (should trigger the event)
    uint8_t buffer_on[] = { 0x5F, 0x90, 0x00 }; // 0x00 = ON
    CECFrame frame_on(buffer_on, sizeof(buffer_on));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame_on);
    }
}

// SetMenuLanguage (0x32)
TEST_F(HdmiCecSink_L2Test, InjectSetMenuLanguageFrame)
{
    // Set Menu Language: opcode 0x32, language "eng"
    uint8_t buffer[] = { 0x40, 0x32, 'e', 'n', 'g' }; // From device 4 to TV (0)
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
    // Optionally: check plugin state or logs for language update
}

// DeviceVendorID (0x87) and verify onDeviceInfoUpdated event
TEST_F(HdmiCecSink_L2Test, InjectDeviceVendorIDFrameAndVerifyEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onDeviceInfoUpdated"),
        &AsyncHandlerMock_HdmiCecSink::onDeviceInfoUpdated,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, onDeviceInfoUpdated(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::onDeviceInfoUpdated));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Device Vendor ID: opcode 0x87, vendor ID 0x00 0x19 0xFB
    uint8_t buffer[] = { 0x4F, 0x87, 0x00, 0x19, 0xFB };
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_DEVICE_INFO_UPDATED);
    EXPECT_TRUE(signalled & ON_DEVICE_INFO_UPDATED);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("onDeviceInfoUpdated"));
}

// DeviceVendorID (0x87)
TEST_F(HdmiCecSink_L2Test, InjectDeviceVendorIDFrameBroadcastIgnoreTest)
{
    // Device Vendor ID: opcode 0x87, vendor ID 0x00 0x19 0xFB
    uint8_t buffer[] = { 0x40, 0x87, 0x00, 0x19, 0xFB };
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// Abort (0xFF)
TEST_F(HdmiCecSink_L2Test, InjectAbortFrame)
{
    // Abort: opcode 0xFF, sent as a direct message (not broadcast)
    uint8_t buffer[] = { 0x40, 0xFF }; // From device 4 to TV (0)
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// Abort (0xFF) Broadcast frame should be ignored
TEST_F(HdmiCecSink_L2Test, InjectAbortFrameBroadcastIgnoreCase)
{
    // Abort: opcode 0xFF, sent as a direct message (not broadcast)
    uint8_t buffer[] = { 0x4F, 0xFF }; // From device 4 to TV (0)
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

// reportFeatureAbortEvent at L2: BLOCKED, with the required change stated.
//
// COVERAGE_GAPS.md traceability: gap-plugin-sink-reportfeatureabort (Sec. 6.2 rank 38, P2).
//
// The obvious test here is to inject a DIRECTED Feature Abort (header 0x40) and assert the event on
// both routes. That test was written and run, and it does not merely fail - it takes SIGSEGV and
// brings the whole WPEFramework process down with it, which would take the entire L2 suite with it.
// The measurement, not a supposition:
//
//     [56] INFO [HdmiCecSinkImplementation.cpp:146] notify:  >>>>>  Received CEC Frame: :40 00 9F 00
//     [56] INFO [HdmiCecSinkImplementation.cpp:431] process: Command: FeatureAbort
//                                                            opcode=GET_CEC_VERSION, Reason = 0
//     Signal received 11. in process [53]
//     WPEFramework shutting down due to a segmentation fault. All relevant data dumped
//
// Root cause, in the shared CEC mock rather than in the plugin. AbortReason declares a public
// delegate `AbortReason* impl` (mocks/HdmiCec.h:292) and its int constructor (:286-288) is the only
// one in that header which does NOT initialise it - the default constructor does
// (mocks/HdmiCec.cpp:321 sets impl(nullptr)), and SystemAudioStatus and AudioStatus both do.
// AbortReason::toInt() (:299-307) calls through `impl` whenever it is non-null and not `this`. The
// frame-parsing FeatureAbort constructor (:1135-1139) builds `reason` through exactly that int
// constructor, so for any frame-parsed Feature Abort the delegate holds an indeterminate value.
// HdmiCecSinkImplementation.cpp:438 - `msg.reason.toInt()`, the first statement after the BROADCAST
// guard - is therefore the first read of a wild pointer, and it is the only reason a DIRECTED frame
// crashes where a broadcast one is dropped safely at cpp:432-435 beforehand. Reproduced away from
// the plugin: with the operand's storage pre-dirtied to an unmapped pattern the same construct exits
// 139 deterministically; with incidentally-readable garbage it returns a plausible value instead,
// which is what makes this a coin flip rather than an honest failure.
//
// Why this cannot be closed from inside the test tree. Because `impl` is public, a caller who builds
// the operand can null it first, and that is precisely how L1 covers this event. Here the operand is
// constructed inside MessageDecoder::decode in the mock library itself, as a temporary the test
// never names - there is no seam. This L2 file is black-box: it holds no reference to
// HdmiCecSinkImplementation::_instance, no registered JSON-RPC method reaches
// reportFeatureAbortEvent (it has exactly one caller, cpp:477, inside process(FeatureAbort)), and
// the mock library is out of scope for edits per AAP Sec. 0.10.2. So the route is genuinely blocked
// rather than merely awkward.
//
// The change that would unblock it, reported and not made: initialise the delegate in the int
// constructor at mocks/HdmiCec.h:286-288, i.e. `AbortReason(int reason) : CECBytes((uint8_t)reason),
// impl(nullptr) {}`, matching what the default constructor already does. One line, in
// entservices-testframework.
//
// Where the gap is closed instead. The sink L1 suite asserts all three generated operands through
// the real notification fan-out, clearing the delegate caller-side as described above:
// reportFeatureAbortEvent_SubscribedClient_ReceivesAllThreeOperands asserts "logicalAddress",
// "opcode" and "FeatureAbortReason" in the delivered event, and
// reportFeatureAbortEvent_EachAbortReason_IsNotified plus
// reportFeatureAbortEvent_BoundaryOperands_AreNotified cover every reason and the operand
// boundaries. What stays uncovered anywhere is the bookkeeping the GET_CEC_VERSION arm performs
// (cpp:443-447 and the m_featureAborts append at cpp:468): no registered method exposes it, and
// reaching it needs the directed frame that crashes. Closing that too requires the one-line mock
// change above, after which a directed-frame case belongs here and an L1 case asserting
// deviceList[4].m_cecVersion and m_featureAborts belongs beside the three named above.
//
// What is still asserted at L2 is the negative arm, immediately below: a broadcast Feature Abort is
// dropped before any reporting. That path never touches the uninitialised delegate.

// A broadcast Feature Abort is dropped before any reporting: the guard at
// HdmiCecSinkImplementation.cpp:432-435, asserted as an observable absence of the event.
//
// This is the reachable half of gap rank 38 at this level; the directed half is blocked for the
// reason set out immediately above. It differs from the pre-existing InjectFeatureAbortFrameBroadcast-
// IgnoreTest further up, which injects the same class of frame but subscribes to nothing and asserts
// nothing, so it cannot distinguish "ignored" from "handled". Here the event is subscribed on the
// JSON-RPC route, the mock is held to .Times(0), and the absence is then waited for.
//
// The wait is deliberately short rather than the file's usual EVNT_TIMEOUT. The reporting fan-out at
// cpp:2250-2258 runs synchronously inside listener->notify(), so once the injection loop below has
// returned the COM-RPC leg has already had its chance; only the JSON-RPC hop is asynchronous, and a
// short grace covers it. Waiting the full five seconds would only add dead time of the kind
// condition 2 in the header note describes.
TEST_F(HdmiCecSink_L2Test, InjectFeatureAbortFrameBroadcastAndVerifyNoEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("reportFeatureAbortEvent"),
        &AsyncHandlerMock_HdmiCecSink::reportFeatureAbortEvent,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, reportFeatureAbortEvent(::testing::_))
        .Times(0);

    ASSERT_FALSE(listeners.empty()) << "No FrameListener was captured.";

    // 0x4F: initiator 4 (Playback Device 1) to destination 0xF, BROADCAST - which is what the guard
    // rejects. 0x00 is FEATURE_ABORT, 0x9F the aborted feature (GET_CEC_VERSION) and 0x00 the reason
    // (UNRECOGNIZED_OPCODE); the mock reads feature from frame[2] and reason from frame[3]
    // (mocks/HdmiCec.h:1135-1139). Rejection happens before either operand is examined, which is why
    // this frame is safe where its directed counterpart is not.
    uint8_t buffer[] = { 0x4F, 0x00, 0x9F, 0x00 };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(1500, REPORT_FEATURE_ABORT);
    EXPECT_FALSE(signalled & REPORT_FEATURE_ABORT);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("reportFeatureAbortEvent"));
}

// Polling: header only, no opcode
TEST_F(HdmiCecSink_L2Test, InjectPollingFrame)
{
    // Polling: header only, no opcode
    uint8_t buffer[] = { 0x40, 0x13 }; // From device 4 to TV (0)
    CECFrame frame(buffer, sizeof(buffer));
    for (auto* listener : listeners) {
        if (listener)
            listener->notify(frame);
    }
}

TEST_F(HdmiCecSink_L2Test, InjectUserControlPressedFrameAndVerifyEvent)
{
    // async_handler is declared BEFORE the link that receives a pointer to it, so the link is
    // destroyed first and can no longer dispatch into a mock that has already gone away.
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;
    uint32_t directSignalled = HDMICECSINK_STATUS_INVALID;

    // Listener availability is a FATAL precondition, so it is checked before anything is
    // subscribed, registered or acquired - see JsonRpcSubscription/SinkInterfaceScope.
    ASSERT_FALSE(listeners.empty()) << "No FrameListener was captured.";

    ResetJsonEventState();
    m_notificationHandler.ResetEvent();

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onKeyPressEvent"),
        &AsyncHandlerMock_HdmiCecSink::onKeyPressEvent,
        &async_handler);
    ASSERT_EQ(Core::ERROR_NONE, status);
    JsonRpcSubscription subscription(jsonrpc, _T("onKeyPressEvent"));

    ASSERT_EQ(Core::ERROR_NONE, CreateHdmiCecSinkInterfaceObject());
    ASSERT_NE(nullptr, m_controller_cecSink);
    ASSERT_NE(nullptr, m_cecSinkPlugin);
    SinkInterfaceScope interfaceScope(m_cecSinkPlugin, m_controller_cecSink, &m_notificationHandler);

    // Attach the notification only once the discovery sweep is quiet - see
    // WaitForDiscoveryToSettle for the unlocked production fan-out this avoids.
    ASSERT_TRUE(WaitForDiscoveryToSettle(m_cecSinkPlugin))
        << "CEC device discovery did not settle; attaching a notification now would race the "
           "unlocked notification fan-out in HdmiCecSinkImplementation.";

    // The handler is a fixture member reused across cases, so start from a known event state.
    m_notificationHandler.ResetEvents();
    ASSERT_EQ(Core::ERROR_NONE, m_cecSinkPlugin->Register(&m_notificationHandler));

    EXPECT_CALL(async_handler, onKeyPressEvent(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::onKeyPressEvent));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // User Control Pressed from Playback Device 1 (4) to TV (0), Volume Up (0x41)
    uint8_t buffer[] = { 0x40, 0x44, 0x41 };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_KEY_PRESS_EVENT);
    EXPECT_TRUE(signalled & ON_KEY_PRESS_EVENT);
    EXPECT_EQ(4, m_logicalAddress);
    EXPECT_EQ(0x41, m_keyCode);

    directSignalled = m_notificationHandler.WaitForRequestStatus(EVNT_TIMEOUT, ON_KEY_PRESS_EVENT);
    EXPECT_TRUE(directSignalled & ON_KEY_PRESS_EVENT);
    EXPECT_EQ(4, m_notificationHandler.GetLogicalAddress());
    EXPECT_EQ(0x41, m_notificationHandler.GetKeyCode());

    // Unsubscribe, Unregister and Release are owned by the scope guards above.
}

TEST_F(HdmiCecSink_L2Test, InjectUserControlReleasedFrameAndVerifyEvent)
{
    // async_handler is declared BEFORE the link that receives a pointer to it, so the link is
    // destroyed first and can no longer dispatch into a mock that has already gone away.
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;
    uint32_t directSignalled = HDMICECSINK_STATUS_INVALID;

    // Listener availability is a FATAL precondition, so it is checked before anything is
    // subscribed, registered or acquired - see JsonRpcSubscription/SinkInterfaceScope.
    ASSERT_FALSE(listeners.empty()) << "No FrameListener was captured.";

    ResetJsonEventState();
    m_notificationHandler.ResetEvent();

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onKeyReleaseEvent"),
        &AsyncHandlerMock_HdmiCecSink::onKeyReleaseEvent,
        &async_handler);
    ASSERT_EQ(Core::ERROR_NONE, status);
    JsonRpcSubscription subscription(jsonrpc, _T("onKeyReleaseEvent"));

    ASSERT_EQ(Core::ERROR_NONE, CreateHdmiCecSinkInterfaceObject());
    ASSERT_NE(nullptr, m_controller_cecSink);
    ASSERT_NE(nullptr, m_cecSinkPlugin);
    SinkInterfaceScope interfaceScope(m_cecSinkPlugin, m_controller_cecSink, &m_notificationHandler);

    // Attach the notification only once the discovery sweep is quiet - see
    // WaitForDiscoveryToSettle for the unlocked production fan-out this avoids.
    ASSERT_TRUE(WaitForDiscoveryToSettle(m_cecSinkPlugin))
        << "CEC device discovery did not settle; attaching a notification now would race the "
           "unlocked notification fan-out in HdmiCecSinkImplementation.";

    // The handler is a fixture member reused across cases, so start from a known event state.
    m_notificationHandler.ResetEvents();
    ASSERT_EQ(Core::ERROR_NONE, m_cecSinkPlugin->Register(&m_notificationHandler));

    EXPECT_CALL(async_handler, onKeyReleaseEvent(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::onKeyReleaseEvent));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // User Control Released from Playback Device 1 (4) to TV (0)
    uint8_t buffer[] = { 0x40, 0x45 };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_KEY_RELEASE_EVENT);
    EXPECT_TRUE(signalled & ON_KEY_RELEASE_EVENT);
    EXPECT_EQ(4, m_logicalAddress);

    directSignalled = m_notificationHandler.WaitForRequestStatus(EVNT_TIMEOUT, ON_KEY_RELEASE_EVENT);
    EXPECT_TRUE(directSignalled & ON_KEY_RELEASE_EVENT);
    EXPECT_EQ(4, m_notificationHandler.GetLogicalAddress());

    // Unsubscribe, Unregister and Release are owned by the scope guards above.
}

TEST_F(HdmiCecSink_L2Test, InjectUserControlPressedMinimumKeyCodeAndVerifyEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onKeyPressEvent"),
        &AsyncHandlerMock_HdmiCecSink::onKeyPressEvent,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, onKeyPressEvent(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::onKeyPressEvent));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Select is the minimum named UI command (0x00)
    uint8_t buffer[] = { 0x40, 0x44, 0x00 };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_KEY_PRESS_EVENT);
    EXPECT_TRUE(signalled & ON_KEY_PRESS_EVENT);
    EXPECT_EQ(4, m_logicalAddress);
    EXPECT_EQ(0x00, m_keyCode);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("onKeyPressEvent"));
}

TEST_F(HdmiCecSink_L2Test, InjectUserControlPressedMaximumNamedKeyCodeAndVerifyEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onKeyPressEvent"),
        &AsyncHandlerMock_HdmiCecSink::onKeyPressEvent,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, onKeyPressEvent(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::onKeyPressEvent));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Power On Function is the highest named UI command (0x6D)
    uint8_t buffer[] = { 0x40, 0x44, 0x6D };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_KEY_PRESS_EVENT);
    EXPECT_TRUE(signalled & ON_KEY_PRESS_EVENT);
    EXPECT_EQ(4, m_logicalAddress);
    EXPECT_EQ(0x6D, m_keyCode);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("onKeyPressEvent"));
}

TEST_F(HdmiCecSink_L2Test, InjectUserControlPressedOutOfRangeKeyCodeAndVerifyEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onKeyPressEvent"),
        &AsyncHandlerMock_HdmiCecSink::onKeyPressEvent,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, onKeyPressEvent(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::onKeyPressEvent));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // 0xFF is outside the named UI command set but remains a valid raw byte
    uint8_t buffer[] = { 0x40, 0x44, 0xFF };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_KEY_PRESS_EVENT);
    EXPECT_TRUE(signalled & ON_KEY_PRESS_EVENT);
    EXPECT_EQ(4, m_logicalAddress);
    EXPECT_EQ(255, m_keyCode);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("onKeyPressEvent"));
}

TEST_F(HdmiCecSink_L2Test, InjectImageViewOnFrameBroadcastAndVerifyNoEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onImageViewOnMsg"),
        &AsyncHandlerMock_HdmiCecSink::onImageViewOnMsg,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, onImageViewOnMsg(::testing::_))
        .Times(0);

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Image View On from Playback Device 1 (4) to broadcast (15)
    uint8_t buffer[] = { 0x4F, 0x04 };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_IMAGE_VIEW_ON);
    EXPECT_FALSE(signalled & ON_IMAGE_VIEW_ON);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("onImageViewOnMsg"));
}

TEST_F(HdmiCecSink_L2Test, InjectTextViewOnFrameBroadcastAndVerifyNoEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onTextViewOnMsg"),
        &AsyncHandlerMock_HdmiCecSink::onTextViewOnMsg,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, onTextViewOnMsg(::testing::_))
        .Times(0);

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Text View On from Playback Device 1 (4) to broadcast (15)
    uint8_t buffer[] = { 0x4F, 0x0D };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_TEXT_VIEW_ON);
    EXPECT_FALSE(signalled & ON_TEXT_VIEW_ON);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("onTextViewOnMsg"));
}

// A second, DISTINCT Image View On scenario: a different initiator, asserted on the payload.
//
// This case used to be a byte-for-byte repeat of InjectImageViewOnFrameAndVerifyEvent above,
// which was pointless while that one was disabled and would have been a duplicate test name once
// it was re-enabled in place. It now covers a different initiator - Playback Device 2 at logical address 8, a
// device the plugin has never heard of until this frame arrives - which is what proves the
// handler's addDevice() step really registers the sender before notifying, rather than the event
// only working for an address the polling sweep happened to know.
TEST_F(HdmiCecSink_L2Test, InjectImageViewOnFromUnknownPlaybackDeviceAndVerifyItsAddress)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    ASSERT_FALSE(listeners.empty()) << "No FrameListener was captured.";

    ResetJsonEventState();

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onImageViewOnMsg"),
        &AsyncHandlerMock_HdmiCecSink::onImageViewOnMsg,
        &async_handler);
    ASSERT_EQ(Core::ERROR_NONE, status);
    JsonRpcSubscription subscription(jsonrpc, _T("onImageViewOnMsg"));

    EXPECT_CALL(async_handler, onImageViewOnMsg(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test::onImageViewOnMsg));

    // Image View On from Playback Device 2 (8) to TV (0)
    uint8_t buffer[] = { 0x80, 0x04 };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_IMAGE_VIEW_ON);
    EXPECT_TRUE(signalled & ON_IMAGE_VIEW_ON);
    EXPECT_EQ(8, JsonImageViewOnLogicalAddress())
        << "onImageViewOnMsg must name the initiator that actually sent the frame";
}

TEST_F(HdmiCecSink_L2Test, InjectImageViewOnFromUnregisteredAddressAndVerifyNoEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onImageViewOnMsg"),
        &AsyncHandlerMock_HdmiCecSink::onImageViewOnMsg,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, onImageViewOnMsg(::testing::_))
        .Times(0);

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Image View On from unregistered logical address (15) to TV (0)
    uint8_t buffer[] = { 0xF0, 0x04 };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_IMAGE_VIEW_ON);
    EXPECT_FALSE(signalled & ON_IMAGE_VIEW_ON);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("onImageViewOnMsg"));
}

TEST_F(HdmiCecSink_L2Test, InjectTextViewOnFromUnregisteredAddressAndVerifyNoEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onTextViewOnMsg"),
        &AsyncHandlerMock_HdmiCecSink::onTextViewOnMsg,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, onTextViewOnMsg(::testing::_))
        .Times(0);

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Text View On from unregistered logical address (15) to TV (0)
    uint8_t buffer[] = { 0xF0, 0x0D };
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_TEXT_VIEW_ON);
    EXPECT_FALSE(signalled & ON_TEXT_VIEW_ON);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("onTextViewOnMsg"));
}

TEST_F(HdmiCecSink_L2Test, HdmiHotplugDisconnectAndVerifyDeviceRemovedEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onDeviceRemoved"),
        &AsyncHandlerMock_HdmiCecSink::onDeviceRemoved,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, onDeviceRemoved(::testing::_))
        .WillRepeatedly(Invoke(this, &HdmiCecSink_L2Test::onDeviceRemoved));

    // CEC must be enabled for the implementation to register its FrameListener; the persisted
    // setting is left false by Set_And_Get_Enabled_JSONRPC earlier in this suite, so establish
    // the precondition here rather than depending on the preceding test.
    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";
    EXPECT_NE(nullptr, g_registeredHdmiInListener);

    // Announce every peer through the production frame path so at least one
    // remains present regardless of where the asynchronous poll sweep started.
    for (uint8_t logicalAddress = 1; logicalAddress < LogicalAddress::UNREGISTERED; ++logicalAddress) {
        uint8_t buffer[] = { static_cast<uint8_t>((logicalAddress << 4) | LogicalAddress::BROADCAST), 0x84, 0x20, 0x00, 0x04 };
        CECFrame frame(buffer, sizeof(buffer));

        for (auto* listener : listeners) {
            if (listener) {
                listener->notify(frame);
            }
        }
    }

    EXPECT_CALL(*p_connectionMock, ping(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [](const LogicalAddress&, const LogicalAddress&, const Throw_e&) {
                throw CECNoAckException();
            }));

    if (g_registeredHdmiInListener) {
        g_registeredHdmiInListener->OnHdmiInEventHotPlug(dsHDMI_IN_PORT_1, true);
        g_registeredHdmiInListener->OnHdmiInEventHotPlug(dsHDMI_IN_PORT_1, false);
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_DEVICE_REMOVED);
    EXPECT_TRUE(signalled & ON_DEVICE_REMOVED);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("onDeviceRemoved"));
}

/* Assert WHICH device an onDeviceRemoved names, on both transports.
 *
 * The sibling HdmiHotplugDisconnectAndVerifyDeviceRemovedEvent above proves that a removal event
 * fires, but asserts only the event bit - it never looks at the payload, so an event naming the
 * wrong device would pass it. This adjacent test adds that observation and leaves the original
 * untouched.
 *
 * Determinism note: a poll sweep removes EVERY device that stopped acknowledging and emits one
 * event per device, so "the last address reported" is not a stable value. The assertion is therefore
 * made against the SET of reported addresses: Playback Device 1 is announced through the production
 * frame path so it is definitely present, every ping is then made to go unacknowledged, and the test
 * requires that address to appear among those reported. Every reported address is also required to
 * be a legal logical address, which is what catches a payload that is absent (recorded as -1),
 * truncated or garbled.
 */
TEST_F(HdmiCecSink_L2Test, HdmiHotplugDisconnectAndVerifyRemovedDeviceAddress)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;
    uint32_t directSignalled = HDMICECSINK_STATUS_INVALID;

    /* Removals are reported by the poll thread's next sweep, and a completed sweep parks for
       HDMICECSINK_PING_INTERVAL_MS (10 s). Quiescing discovery before registering deliberately puts
       this test just after a sweep, so the wait has to span a full interval plus margin - one
       EVNT_TIMEOUT would expire inside the park and report a false negative. */
    const uint32_t kRemovalTimeoutMs = 25000;

    // Fatal preconditions first, before anything is subscribed, registered or acquired.
    ASSERT_FALSE(listeners.empty()) << "No FrameListener was captured.";
    ASSERT_NE(nullptr, g_registeredHdmiInListener);

    ResetJsonEventState();
    m_notificationHandler.ResetEvent();

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onDeviceRemoved"),
        &AsyncHandlerMock_HdmiCecSink::onDeviceRemoved,
        &async_handler);
    ASSERT_EQ(Core::ERROR_NONE, status);
    JsonRpcSubscription subscription(jsonrpc, _T("onDeviceRemoved"));

    ASSERT_EQ(Core::ERROR_NONE, CreateHdmiCecSinkInterfaceObject());
    ASSERT_NE(nullptr, m_controller_cecSink);
    ASSERT_NE(nullptr, m_cecSinkPlugin);
    SinkInterfaceScope interfaceScope(m_cecSinkPlugin, m_controller_cecSink, &m_notificationHandler);

    // Attach the notification only once the discovery sweep is quiet - see WaitForDiscoveryToSettle
    // for the unlocked production fan-out this avoids.
    ASSERT_TRUE(WaitForDiscoveryToSettle(m_cecSinkPlugin))
        << "CEC device discovery did not settle; attaching a notification now would race the "
           "unlocked notification fan-out in HdmiCecSinkImplementation.";
    ASSERT_EQ(Core::ERROR_NONE, m_cecSinkPlugin->Register(&m_notificationHandler));

    EXPECT_CALL(async_handler, onDeviceRemoved(::testing::_))
        .WillRepeatedly(Invoke(this, &HdmiCecSink_L2Test::onDeviceRemoved));

    // Announce every peer through the production frame path - <Report Physical Address> (0x84)
    // broadcast - so a device is present on the port about to be unplugged regardless of where the
    // asynchronous poll sweep happened to be. This is the sibling test's stimulus verbatim; what
    // this test adds is the payload observation below, not a different way of provoking the event.
    for (uint8_t logicalAddress = 1; logicalAddress < LogicalAddress::UNREGISTERED; ++logicalAddress) {
        uint8_t buffer[] = {
            static_cast<uint8_t>((logicalAddress << 4) | LogicalAddress::BROADCAST),
            0x84, 0x20, 0x00, 0x04
        };
        CECFrame frame(buffer, sizeof(buffer));
        for (auto* listener : listeners) {
            if (listener) {
                listener->notify(frame);
            }
        }
    }

    // Every peer now fails to acknowledge, so the next sweep reports removals.
    EXPECT_CALL(*p_connectionMock, ping(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [](const LogicalAddress&, const LogicalAddress&, const Throw_e&) {
                throw CECNoAckException();
            }));

    g_registeredHdmiInListener->OnHdmiInEventHotPlug(dsHDMI_IN_PORT_1, true);
    g_registeredHdmiInListener->OnHdmiInEventHotPlug(dsHDMI_IN_PORT_1, false);

    // ---- JSON-RPC: an event arrived, and it named the device that was present -----------------
    signalled = WaitForRequestStatus(kRemovalTimeoutMs, ON_DEVICE_REMOVED);
    ASSERT_TRUE(signalled & ON_DEVICE_REMOVED) << "No onDeviceRemoved arrived over JSON-RPC.";

    std::vector<int> jsonRemoved = JsonRemovedLogicalAddresses();
    EXPECT_FALSE(jsonRemoved.empty());
    for (const int address : jsonRemoved) {
        EXPECT_GE(address, 0)
            << "onDeviceRemoved carried no logicalAddress label - the payload was dropped.";
        EXPECT_LT(address, static_cast<int>(LogicalAddress::UNREGISTERED))
            << "onDeviceRemoved named " << address << ", which is not a legal logical address.";
    }
    EXPECT_EQ(jsonRemoved.back(), JsonRemovedLogicalAddress())
        << "The most recent recorded address disagrees with the recorded sequence.";

    // ---- COM-RPC: the same, through a handler that overrides the method -----------------------
    directSignalled = m_notificationHandler.WaitForRequestStatus(kRemovalTimeoutMs, ON_DEVICE_REMOVED);
    ASSERT_TRUE(directSignalled & ON_DEVICE_REMOVED) << "No OnDeviceRemoved arrived over COM-RPC.";

    std::vector<int> comRemoved = m_notificationHandler.GetRemovedLogicalAddresses();
    EXPECT_FALSE(comRemoved.empty());
    for (const int address : comRemoved) {
        EXPECT_GE(address, 0)
            << "OnDeviceRemoved carried no usable logical address.";
        EXPECT_LT(address, static_cast<int>(LogicalAddress::UNREGISTERED))
            << "OnDeviceRemoved named " << address << ", which is not a legal logical address.";
    }
    EXPECT_EQ(comRemoved.back(), m_notificationHandler.GetRemovedLogicalAddress())
        << "The most recent OnDeviceRemoved disagrees with the recorded sequence.";

    // The two transports carry the SAME production event, so they must name the same devices. This
    // is the assertion that actually pins the payload down: a transport that drops, reorders into a
    // different membership, duplicates or mangles the address can no longer pass.
    std::sort(jsonRemoved.begin(), jsonRemoved.end());
    std::sort(comRemoved.begin(), comRemoved.end());
    EXPECT_EQ(comRemoved, jsonRemoved)
        << "JSON-RPC and COM-RPC reported different sets of removed logical addresses.";

    // Unsubscribe, Unregister and Release are owned by the scope guards above.
}

// Active Source (0x82) and verify onWakeupFromStandby event
TEST_F(HdmiCecSink_L2Test_STANDBY, InjectWakeupFromStandbyFrameAndVerifyEvent)
{
    JSONRPC::LinkType<Core::JSON::IElement> jsonrpc(HDMICECSINK_CALLSIGN, HDMICECSINK_L2TEST_CALLSIGN);
    StrictMock<AsyncHandlerMock_HdmiCecSink> async_handler;
    uint32_t status = Core::ERROR_GENERAL;
    uint32_t signalled = HDMICECSINK_STATUS_INVALID;

    status = jsonrpc.Subscribe<JsonObject>(EVNT_TIMEOUT,
        _T("onWakeupFromStandby"),
        &AsyncHandlerMock_HdmiCecSink::onWakeupFromStandby,
        &async_handler);
    EXPECT_EQ(Core::ERROR_NONE, status);

    EXPECT_CALL(async_handler, onWakeupFromStandby(::testing::_))
        .WillOnce(Invoke(this, &HdmiCecSink_L2Test_STANDBY::onWakeupFromStandby));

    ASSERT_TRUE(EnableCecAndAwaitFrameListener()) << "CEC could not be enabled, so no FrameListener was captured.";

    // Simulate TV in standby, then send Active Source to wake it up
    uint8_t buffer[] = { 0x4F, 0x82, 0x10, 0x00 }; // Active Source from device 4 to broadcast
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }

    signalled = WaitForRequestStatus(EVNT_TIMEOUT, ON_WAKEUP_FROM_STANDBY);
    EXPECT_TRUE(signalled & ON_WAKEUP_FROM_STANDBY);

    jsonrpc.Unsubscribe(EVNT_TIMEOUT, _T("onWakeupFromStandby"));
}

TEST_F(HdmiCecSink_L2Test, ActiveSourceFrameBroadcastIgnoreTest)
{
    uint8_t buffer[] = { 0x40, 0x82, 0x10, 0x00 }; // Active Source from device 4 to broadcast
    CECFrame frame(buffer, sizeof(buffer));

    for (auto* listener : listeners) {
        if (listener) {
            listener->notify(frame);
        }
    }
}

// Power Mode Change to ON to verify onPowerModeChanged event
TEST_F(HdmiCecSink_L2Test_STANDBY, TriggerOnPowerModeChangeEvent_ON)
{
    Core::ProxyType<RPC::InvokeServerType<1, 0, 4>> mEngine_PowerManager;
    Core::ProxyType<RPC::CommunicatorClient> mClient_PowerManager;
    PluginHost::IShell* mController_PowerManager;

    TEST_LOG("Creating mEngine_PowerManager");
    mEngine_PowerManager = Core::ProxyType<RPC::InvokeServerType<1, 0, 4>>::Create();
    mClient_PowerManager = Core::ProxyType<RPC::CommunicatorClient>::Create(Core::NodeId("/tmp/communicator"), Core::ProxyType<Core::IIPCServer>(mEngine_PowerManager));

    TEST_LOG("Creating mEngine_PowerManager Announcements");
#if ((THUNDER_VERSION == 2) || ((THUNDER_VERSION == 4) && (THUNDER_VERSION_MINOR == 2)))
    mEngine_PowerManager->Announcements(mClient_PowerManager->Announcement());
#endif

    if (!mClient_PowerManager.IsValid()) {
        TEST_LOG("Invalid mClient_PowerManager");
    } else {
        mController_PowerManager = mClient_PowerManager->Open<PluginHost::IShell>(_T("org.rdk.PowerManager"), ~0, 3000);
        if (mController_PowerManager) {
            auto PowerManagerPlugin = mController_PowerManager->QueryInterface<Exchange::IPowerManager>();

            if (PowerManagerPlugin) {
                int keyCode = 0;

                uint32_t clientId = 0;
                uint32_t status = PowerManagerPlugin->AddPowerModePreChangeClient("l2-test-client", clientId);
                EXPECT_EQ(status, Core::ERROR_NONE);

                EXPECT_CALL(*p_powerManagerHalMock, PLAT_API_SetPowerState(::testing::_))
                    .WillOnce(::testing::Invoke(
                        [](PWRMgr_PowerState_t powerState) {
                            EXPECT_EQ(powerState, PWRMGR_POWERSTATE_ON);
                            return PWRMGR_SUCCESS;
                        }));

                status = PowerManagerPlugin->SetPowerState(keyCode, PowerState::POWER_STATE_ON, "l2-test");
                EXPECT_EQ(status, Core::ERROR_NONE);

                // some delay to destroy AckController after IModeChanged notification
                std::this_thread::sleep_for(std::chrono::milliseconds(1500));

                PowerManagerPlugin->Release();
            } else {
                TEST_LOG("PowerManagerPlugin is NULL");
            }
            mController_PowerManager->Release();
        } else {
            TEST_LOG("mController_PowerManager is NULL");
        }
    }
}

// Power Mode Change to OFF to verify onPowerModeChanged event
TEST_F(HdmiCecSink_L2Test, RaisePowerModeChangedEvent_OFF)
{
    Core::ProxyType<RPC::InvokeServerType<1, 0, 4>> mEngine_PowerManager;
    Core::ProxyType<RPC::CommunicatorClient> mClient_PowerManager;
    PluginHost::IShell* mController_PowerManager;

    TEST_LOG("Creating mEngine_PowerManager");
    mEngine_PowerManager = Core::ProxyType<RPC::InvokeServerType<1, 0, 4>>::Create();
    mClient_PowerManager = Core::ProxyType<RPC::CommunicatorClient>::Create(Core::NodeId("/tmp/communicator"), Core::ProxyType<Core::IIPCServer>(mEngine_PowerManager));

    TEST_LOG("Creating mEngine_PowerManager Announcements");
#if ((THUNDER_VERSION == 2) || ((THUNDER_VERSION == 4) && (THUNDER_VERSION_MINOR == 2)))
    mEngine_PowerManager->Announcements(mClient_PowerManager->Announcement());
#endif

    if (!mClient_PowerManager.IsValid()) {
        TEST_LOG("Invalid mClient_PowerManager");
    } else {
        mController_PowerManager = mClient_PowerManager->Open<PluginHost::IShell>(_T("org.rdk.PowerManager"), ~0, 3000);
        if (mController_PowerManager) {
            auto PowerManagerPlugin = mController_PowerManager->QueryInterface<Exchange::IPowerManager>();

            if (PowerManagerPlugin) {
                int keyCode = 0;

                uint32_t clientId = 0;
                uint32_t status = PowerManagerPlugin->AddPowerModePreChangeClient("l2-test-client", clientId);
                EXPECT_EQ(status, Core::ERROR_NONE);

                EXPECT_CALL(*p_powerManagerHalMock, PLAT_API_SetPowerState(::testing::_))
                    .WillOnce(::testing::Invoke(
                        [](PWRMgr_PowerState_t powerState) {
                            EXPECT_EQ(powerState, PWRMGR_POWERSTATE_OFF);
                            return PWRMGR_SUCCESS;
                        }));

                status = PowerManagerPlugin->SetPowerState(keyCode, PowerState::POWER_STATE_OFF, "l2-test");
                EXPECT_EQ(status, Core::ERROR_NONE);

                // some delay to destroy AckController after IModeChanged notification
                std::this_thread::sleep_for(std::chrono::milliseconds(1500));

                PowerManagerPlugin->Release();
            } else {
                TEST_LOG("PowerManagerPlugin is NULL");
            }
            mController_PowerManager->Release();
        } else {
            TEST_LOG("mController_PowerManager is NULL");
        }
    }
}
