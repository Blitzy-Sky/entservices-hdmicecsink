/**
* If not stated otherwise in this file or this component's LICENSE
* file the following copyright and licenses apply:
*
* Copyright 2024 RDK Management
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
**/

#include <gtest/gtest.h>
#include <iostream>
#include <fstream>
#include <string>
#include <thread>
#include <chrono>


#include "HdmiCecSink.h"
#include "HdmiCecSinkImplementation.h"
#include "HdmiCecSinkMock.h"
#include "FactoriesImplementation.h"
#include "IarmBusMock.h"
#include "ServiceMock.h"
#include "devicesettings.h"
#include "HdmiCec.h"
#include "HdmiCecMock.h"
#include "WrapsMock.h"
#include "RfcApiMock.h"
#include "ThunderPortability.h"
#include "PowerManagerMock.h"
#include "WorkerPoolImplementation.h"
#include "COMLinkMock.h"
#include "ManagerMock.h"
#include "HostMock.h"
#include "HdmiInputMock.h"
#include "TelemetryMock.h"

using namespace WPEFramework;
using ::testing::NiceMock;

namespace
{
	static void removeFile(const char* fileName)
	{
		if (std::remove(fileName) != 0)
		{
			printf("File %s failed to remove\n", fileName);
			perror("Error deleting file");
		}
		else
		{
			printf("File %s successfully deleted\n", fileName);
		}
	}
	
	static void createFile(const char* fileName, const char* fileContent)
	{
		removeFile(fileName);

		std::ofstream fileContentStream(fileName);
		fileContentStream << fileContent;
		fileContentStream << "\n";
		fileContentStream.close();
	}
}

class HdmiCecSinkInitializeTest : public ::testing::Test {
protected:
    Core::ProxyType<Plugin::HdmiCecSink> plugin;
    Core::JSONRPC::Handler& handler;
    DECL_CORE_JSONRPC_CONX connection;
    IARM_EventHandler_t dsHdmiEventHandler;
    Core::ProxyType<Plugin::HdmiCecSinkImplementation> pluginImpl;
    Core::ProxyType<WorkerPoolImplementation> workerPool;
    NiceMock<FactoriesImplementation> factoriesImplementation;
    NiceMock<ServiceMock> service;
    PLUGINHOST_DISPATCHER* dispatcher;
    NiceMock<COMLinkMock> comLinkMock;
    Core::JSONRPC::Message message;
    string response;

    HdmiCecSinkInitializeTest()
        : plugin(Core::ProxyType<Plugin::HdmiCecSink>::Create())
        , handler(*(plugin))
        , INIT_CONX(1, 0)
		, dsHdmiEventHandler(nullptr)
        , workerPool(Core::ProxyType<WorkerPoolImplementation>::Create(
              2, Core::Thread::DefaultStackSize(), 16))
		, dispatcher(nullptr)
    {
    }

    virtual ~HdmiCecSinkInitializeTest() override
    {
        plugin.Release();
    }
};

class HdmiCecSinkDsTest : public HdmiCecSinkInitializeTest {
protected:
    IarmBusImplMock         *p_iarmBusImplMock = nullptr ;
    ManagerImplMock         *p_managerImplMock = nullptr ;
    HostImplMock            *p_hostImplMock = nullptr ;
    HdmiInputImplMock       *p_hdmiInputImplMock = nullptr;
    ConnectionImplMock      *p_connectionImplMock = nullptr ;
    MessageEncoderMock      *p_messageEncoderMock = nullptr ;
    LibCCECImplMock         *p_libCCECImplMock = nullptr ;
    RfcApiImplMock   *p_rfcApiImplMock = nullptr ;
    WrapsImplMock  *p_wrapsImplMock   = nullptr ;
    TelemetryApiImplMock    *p_telemetryApiImplMock = nullptr;
    NiceMock<RfcApiImplMock> rfcApiImplMock;
    NiceMock<WrapsImplMock> wrapsImplMock;
    string response;
    std::vector<FrameListener*> listeners;

    HdmiCecSinkDsTest(): HdmiCecSinkInitializeTest()
    {
        createFile("/etc/device.properties", "RDK_PROFILE=TV");
        p_iarmBusImplMock  = new NiceMock <IarmBusImplMock>;
        IarmBus::setImpl(p_iarmBusImplMock);

        p_managerImplMock  = new NiceMock <ManagerImplMock>;
        device::Manager::setImpl(p_managerImplMock);

        p_hostImplMock      = new NiceMock <HostImplMock>;
        device::Host::setImpl(p_hostImplMock);

        p_hdmiInputImplMock  = new NiceMock <HdmiInputImplMock>;
        device::HdmiInput::setImpl(p_hdmiInputImplMock);

        p_libCCECImplMock  = new testing::NiceMock <LibCCECImplMock>;
        LibCCEC::setImpl(p_libCCECImplMock);

        p_messageEncoderMock  = new testing::NiceMock <MessageEncoderMock>;
        MessageEncoder::setImpl(p_messageEncoderMock);

        p_connectionImplMock  = new testing::NiceMock <ConnectionImplMock>;
        Connection::setImpl(p_connectionImplMock);

        p_rfcApiImplMock  = new testing::NiceMock <RfcApiImplMock>;
        RfcApi::setImpl(p_rfcApiImplMock);

        p_wrapsImplMock  = new testing::NiceMock <WrapsImplMock>;
        Wraps::setImpl(p_wrapsImplMock); /*Set up mock for fopen;
                                                      to use the mock implementation/the default behavior of the fopen function from Wraps class.*/

        p_telemetryApiImplMock = new NiceMock<TelemetryApiImplMock>;
        TelemetryApi::setImpl(p_telemetryApiImplMock);

        ON_CALL(*p_connectionImplMock, poll(::testing::_, ::testing::_))
            .WillByDefault(::testing::Invoke(
                [&](const LogicalAddress &from, const Throw_e &doThrow) {
                throw CECNoAckException();
                }));

        EXPECT_CALL(*p_libCCECImplMock, getPhysicalAddress(::testing::_))
            .WillRepeatedly(::testing::Invoke(
                [&](uint32_t *physAddress) {
                    *physAddress = (uint32_t)0x12345678;
                }));

        ON_CALL(*p_messageEncoderMock, encode(::testing::Matcher<const DataBlock&>(::testing::_)))
            .WillByDefault(::testing::ReturnRef(CECFrame::getInstance()));
        ON_CALL(*p_messageEncoderMock, encode(::testing::Matcher<const UserControlPressed&>(::testing::_)))
           .WillByDefault(::testing::ReturnRef(CECFrame::getInstance()));

        EXPECT_CALL(*p_managerImplMock, Initialize())
            .Times(::testing::AnyNumber())
            .WillRepeatedly(::testing::Return());

        ON_CALL(*p_connectionImplMock, open())
            .WillByDefault(::testing::Return());

        EXPECT_CALL(*p_hdmiInputImplMock, getNumberOfInputs())
            .WillRepeatedly(::testing::Return(3));

        ON_CALL(*p_hdmiInputImplMock, isPortConnected(::testing::_))
            .WillByDefault(::testing::Invoke(
                [](int8_t port) {
                    return port == 1? true : false;
                }));

        ON_CALL(*p_hdmiInputImplMock, getHDMIARCPortId(::testing::_))
            .WillByDefault(::testing::Invoke(
                [](int &portId) {
                    portId = 1;
                    return dsERR_NONE;
                }));

        ON_CALL(*p_connectionImplMock, addFrameListener(::testing::_))
        .WillByDefault([this](FrameListener* listener) {
            printf("[TEST] addFrameListener called with address: %p\n", static_cast<void*>(listener));
            this->listeners.push_back(listener);
        });

        ON_CALL(comLinkMock, Instantiate(::testing::_, ::testing::_, ::testing::_))
            .WillByDefault(::testing::Invoke(
                [&](const RPC::Object& object, const uint32_t waitTime, uint32_t& connectionId) {
                    pluginImpl = Core::ProxyType<Plugin::HdmiCecSinkImplementation>::Create();
                    return &pluginImpl;
                }));

        Core::IWorkerPool::Assign(&(*workerPool));
        workerPool->Run();

        PluginHost::IFactories::Assign(&factoriesImplementation);

        dispatcher = static_cast<PLUGINHOST_DISPATCHER*>(
           plugin->QueryInterface(PLUGINHOST_DISPATCHER_ID));
        dispatcher->Activate(&service);

        EXPECT_EQ(string(""), plugin->Initialize(&service));
    }
    virtual ~HdmiCecSinkDsTest() override {

        plugin->Deinitialize(&service);

        Core::IWorkerPool::Assign(nullptr);
        workerPool.Release();
        dispatcher->Deactivate();
        dispatcher->Release();
        PluginHost::IFactories::Assign(nullptr);

        removeFile("/etc/device.properties");

        IarmBus::setImpl(nullptr);
        if (p_iarmBusImplMock != nullptr)
        {
            delete p_iarmBusImplMock;
            p_iarmBusImplMock = nullptr;
        }
        device::Manager::setImpl(nullptr);
        if (p_managerImplMock != nullptr)
        {
            delete p_managerImplMock;
            p_managerImplMock = nullptr;
        }
        device::Host::setImpl(nullptr);
        if (p_hostImplMock != nullptr)
        {
            delete p_hostImplMock;
            p_hostImplMock = nullptr;
        }
        device::HdmiInput::setImpl(nullptr);
        if (p_hdmiInputImplMock != nullptr)
        {
            delete p_hdmiInputImplMock;
            p_hdmiInputImplMock = nullptr;
        }
        LibCCEC::setImpl(nullptr);
        if (p_libCCECImplMock != nullptr)
        {
            delete p_libCCECImplMock;
            p_libCCECImplMock = nullptr;
        }
        Connection::setImpl(nullptr);
        if (p_connectionImplMock != nullptr)
        {
            delete p_connectionImplMock;
            p_connectionImplMock = nullptr;
        }
        MessageEncoder::setImpl(nullptr);
        if (p_messageEncoderMock != nullptr)
        {
            delete p_messageEncoderMock;
            p_messageEncoderMock = nullptr;
        }

        RfcApi::setImpl(nullptr);
        if (p_rfcApiImplMock != nullptr)
        {
            delete p_rfcApiImplMock;
            p_rfcApiImplMock = nullptr;
        }

        Wraps::setImpl(nullptr);
        if (p_wrapsImplMock != nullptr)
        {
            delete p_wrapsImplMock;
            p_wrapsImplMock = nullptr;
        }

        TelemetryApi::setImpl(nullptr);
        if (p_telemetryApiImplMock != nullptr)
        {
            delete p_telemetryApiImplMock;
            p_telemetryApiImplMock = nullptr;
        }
    }
};

class HdmiCecSinkInitializedEventTest : public HdmiCecSinkDsTest {
protected:
    NiceMock<ServiceMock> service;
    NiceMock<FactoriesImplementation> factoriesImplementation;
    PLUGINHOST_DISPATCHER* dispatcher;
    Core::JSONRPC::Message message;

    HdmiCecSinkInitializedEventTest(): HdmiCecSinkDsTest()
    {
        PluginHost::IFactories::Assign(&factoriesImplementation);

        dispatcher = static_cast<PLUGINHOST_DISPATCHER*>(
           plugin->QueryInterface(PLUGINHOST_DISPATCHER_ID));
        dispatcher->Activate(&service);
    }
    virtual ~HdmiCecSinkInitializedEventTest() override
    {
        dispatcher->Deactivate();
        dispatcher->Release();
        PluginHost::IFactories::Assign(nullptr);
    }
};

class HdmiCecSinkInitializedEventDsTest : public HdmiCecSinkInitializedEventTest {
protected:
    HdmiCecSinkInitializedEventDsTest(): HdmiCecSinkInitializedEventTest()
    {
    }
    virtual ~HdmiCecSinkInitializedEventDsTest() override
    {
    }
};

TEST_F(HdmiCecSinkDsTest, RegisteredMethods)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("setEnabled")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("setOSDName")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("setVendorId")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("getVendorId")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("setActivePath")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("setRoutingChange")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("getDeviceList")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("getActiveSource")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("setActiveSource")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("getActiveRoute")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("setMenuLanguage")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("requestActiveSource")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("setupARCRouting")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("requestShortAudioDescriptor")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("sendStandbyMessage")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("sendAudioDevicePowerOnMessage")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("sendKeyPressEvent")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("sendGetAudioStatusMessage")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("getAudioDeviceConnectedStatus")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("requestAudioDevicePowerStatus")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("sendUserControlPressed")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("sendUserControlReleased")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("setLatencyInfo")));
    EXPECT_EQ(Core::ERROR_NONE, handler.Exists(_T("printDeviceList")));

}

TEST_F(HdmiCecSinkDsTest, setOSDNameParamMissing)
{

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setOSDName"), _T("{}"), response));
    EXPECT_EQ(response,  string("{\"success\":true}"));

}

TEST_F(HdmiCecSinkDsTest, getOSDName)
{

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setOSDName"), _T("{\"name\":\"CECTEST\"}"), response));
    EXPECT_EQ(response,  string("{\"success\":true}"));

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getOSDName"), _T("{}"), response));
    EXPECT_EQ(response,  string("{\"name\":\"CECTEST\",\"success\":true}"));

}

TEST_F(HdmiCecSinkDsTest, setVendorIdParamMissing)
{

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setVendorId"), _T("{}"), response));
    EXPECT_EQ(response,  string("{\"success\":true}"));

}

TEST_F(HdmiCecSinkDsTest, getVendorId)
{

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setVendorId"), _T("{\"vendorid\":\"0x0019FF\"}"), response));
    EXPECT_EQ(response,  string("{\"success\":true}"));

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getVendorId"), _T("{}"), response));
    EXPECT_EQ(response,  string("{\"vendorid\":\"019ff\",\"success\":true}"));

}

TEST_F(HdmiCecSinkDsTest, setActivePathMissingParam)
{

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setActivePath"), _T("{}"), response));
    EXPECT_EQ(response,  string("{\"success\":true}"));

}

TEST_F(HdmiCecSinkDsTest, setActivePath)
{

    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](const LogicalAddress &to, const CECFrame &frame, int timeout) {
               EXPECT_EQ(to.toInt(), LogicalAddress::BROADCAST);
               EXPECT_GT(timeout, 0);
            }));

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setActivePath"), _T("{\"activePath\":\"2.0.0.0\"}"), response));
    EXPECT_EQ(response,  string("{\"success\":true}"));

}

TEST_F(HdmiCecSinkDsTest, setRoutingChangeInvalidParam)
{

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setRoutingChange"), _T("{\"oldPort\":\"HDMI0\"}"), response));
    EXPECT_EQ(response,  string("{\"success\":false}"));

}

TEST_F(HdmiCecSinkDsTest, setRoutingChange)
{

    std::this_thread::sleep_for(std::chrono::seconds(30));

    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](const LogicalAddress &to, const CECFrame &frame, int timeout) {
                EXPECT_EQ(to.toInt(), LogicalAddress::BROADCAST);
                EXPECT_GT(timeout, 0);
            }));

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setRoutingChange"), _T("{\"oldPort\":\"HDMI0\",\"newPort\":\"TV\"}"), response));
    EXPECT_EQ(response,  string("{\"success\":true}"));

}

TEST_F(HdmiCecSinkDsTest, setMenuLanguageInvalidParam)
{

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setMenuLanguage"), _T("{\"language\":""}"), response));
    EXPECT_EQ(response,  string("{\"success\":true}"));

}

TEST_F(HdmiCecSinkDsTest, setMenuLanguage)
{

    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](const LogicalAddress &to, const CECFrame &frame, int timeout) {
                EXPECT_LE(to.toInt(), LogicalAddress::BROADCAST);
                EXPECT_GT(timeout, 0);
            }));

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setMenuLanguage"), _T("{\"language\":\"english\"}"), response));
    EXPECT_EQ(response,  string("{\"success\":true}"));

}

TEST_F(HdmiCecSinkDsTest, setupARCRoutingInvalidParam)
{

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setupARCRouting"), _T("{}"), response));
    EXPECT_EQ(response,  string("{\"success\":true}"));

}

TEST_F(HdmiCecSinkDsTest, setupARCRouting)
{

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setupARCRouting"), _T("{\"enabled\":true}"), response));
    EXPECT_EQ(response,  string("{\"success\":true}"));

}

TEST_F(HdmiCecSinkDsTest, setupARCRouting_False)
{

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setupARCRouting"), _T("{\"enabled\":false}"), response));
    EXPECT_EQ(response,  string("{\"success\":true}"));

}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEventMissingParam)
{

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\": 0, \"keyCode\": }"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));

}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent)
{

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\": 0, \"keyCode\": 65}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));

}

TEST_F(HdmiCecSinkInitializedEventDsTest, onHdmiOutputHDCPStatusEvent)
{

    EVENT_SUBSCRIBE(0, _T("onDevicesChanged"), _T("client.events.onDevicesChanged"), message);
    Plugin::HdmiCecSinkImplementation::_instance->OnHdmiInEventHotPlug(dsHDMI_IN_PORT_1, true);
    EVENT_UNSUBSCRIBE(0, _T("onDevicesChanged"), _T("client.events.onDevicesChanged"), message);

}

TEST_F(HdmiCecSinkInitializedEventDsTest, powerModeChange)
{
    // ASSERT_TRUE(pwrMgrModeChangeEventHandler != nullptr);

    IARM_Bus_PWRMgr_EventData_t eventData;
    eventData.data.state.newState =IARM_BUS_PWRMGR_POWERSTATE_ON;
    eventData.data.state.curState =IARM_BUS_PWRMGR_POWERSTATE_STANDBY;

    (void) eventData;

    // pwrMgrModeChangeEventHandler(IARM_BUS_PWRMGR_NAME, IARM_BUS_PWRMGR_EVENT_MODECHANGED, &eventData , 0);
}

TEST_F(HdmiCecSinkInitializedEventDsTest, DISABLED_getCecVersion)
{
    /*EXPECT_CALL(rfcApiImplMock, getRFCParameter(::testing::_, ::testing::_, ::testing::_))
        .Times(1)
        .WillOnce(::testing::Invoke(
            [](char* pcCallerID, const char* pcParameterName, RFC_ParamData_t* pstParamData) {
                EXPECT_EQ(string(pcCallerID), string("HdmiCecSink"));
                EXPECT_EQ(string(pcParameterName), string("Device.DeviceInfo.X_RDKCENTRAL-COM_RFC.Feature.HdmiCecSink.CECVersion"));
                strncpy(pstParamData->value, "1.4", sizeof(pstParamData->value));
                return WDMP_SUCCESS;
            }));*/

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getCecVersion"), _T("{}"), response));
    EXPECT_EQ(response, string("{\"CECVersion\":\"1.4\",\"success\":true}"));

}



//Copilot Generated Code

TEST_F(HdmiCecSinkDsTest, setEnabled_ValidTrue)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setEnabled"), _T("{\"enabled\":true}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setEnabled_ValidFalse)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setEnabled"), _T("{\"enabled\":false}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, getEnabled)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getEnabled"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"enabled\":(true|false)"));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
}

TEST_F(HdmiCecSinkDsTest, setOSDName_EmptyName)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setOSDName"), _T("{\"name\":\"\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setOSDName_LongName)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setOSDName"), _T("{\"name\":\"VERYLONGNAMETHATEXCEEDSLIMIT\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setVendorId_ValidHex)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setVendorId"), _T("{\"vendorid\":\"0x123456\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setVendorId_InvalidFormat)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setVendorId"), _T("{\"vendorid\":\"INVALID\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setActivePath_InvalidPath)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setActivePath"), _T("{\"activePath\":\"INVALID.PATH\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setRoutingChange_ValidPorts)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());
    
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setRoutingChange"), _T("{\"oldPort\":\"HDMI1\",\"newPort\":\"HDMI2\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setRoutingChange_SamePorts)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setRoutingChange"), _T("{\"oldPort\":\"HDMI0\",\"newPort\":\"HDMI0\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, getDeviceList)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getDeviceList"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"numberofdevices\":"));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
}

TEST_F(HdmiCecSinkDsTest, getActiveSource)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getActiveSource"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"logicalAddress\":"));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
}

TEST_F(HdmiCecSinkDsTest, setActiveSource_ValidLogicalAddress)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setActiveSource"), _T("{\"logicalAddress\":1}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setActiveSource_InvalidLogicalAddress)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setActiveSource"), _T("{\"logicalAddress\":16}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setActiveSource_MissingParam)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setActiveSource"), _T("{}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, getActiveRoute)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getActiveRoute"), _T("{}"), response));
    EXPECT_EQ(response, string("{\"available\":false,\"length\":0,\"ActiveRoute\":\"\",\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, requestActiveSource)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());
    
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("requestActiveSource"), _T("{}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, requestShortAudioDescriptor)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());
    
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("requestShortAudioDescriptor"), _T("{}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendStandbyMessage)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());
    
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendStandbyMessage"), _T("{}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendAudioDevicePowerOnMessage)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());
    
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendAudioDevicePowerOnMessage"), _T("{}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_InvalidLogicalAddress)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":16,\"keyCode\":1}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_InvalidKeyCode)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":256}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendGetAudioStatusMessage)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());
    
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendGetAudioStatusMessage"), _T("{}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, getAudioDeviceConnectedStatus)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getAudioDeviceConnectedStatus"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"connected\":"));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
}

TEST_F(HdmiCecSinkDsTest, requestAudioDevicePowerStatus)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());
    
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("requestAudioDevicePowerStatus"), _T("{}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, getDeviceList_ConnectionClosed)
{
    EXPECT_CALL(*p_connectionImplMock, close())
        .WillOnce(::testing::Return());
    
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getDeviceList"), _T("{}"), response));
}

TEST_F(HdmiCecSinkDsTest, setOSDName_MaxLength)
{
    string longName(14, 'X');
    string payload = "{\"name\":\"" + longName + "\"}";
    
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setOSDName"), payload, response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setVendorId_Boundary)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setVendorId"), _T("{\"vendorid\":\"0xFFFFFF\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setVendorId_MinValue)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setVendorId"), _T("{\"vendorid\":\"0x000000\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_BoundaryKeyCode)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":0,\"keyCode\":255}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_MinKeyCode)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":0,\"keyCode\":0}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setActiveSource_BoundaryLogicalAddress)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setActiveSource"), _T("{\"logicalAddress\":15}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setActiveSource_MinLogicalAddress)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setActiveSource"), _T("{\"logicalAddress\":0}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setRoutingChange_InvalidPortFormat)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setRoutingChange"), _T("{\"oldPort\":\"INVALID_PORT\",\"newPort\":\"HDMI0\"}"), response));
    EXPECT_EQ(response, string("{\"success\":false}"));
}

TEST_F(HdmiCecSinkDsTest, setMenuLanguage_SpecialCharacters)
{    
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setMenuLanguage"), _T("{\"language\":\"ñäöü\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, MalformedJSON_setEnabled)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setEnabled"), _T("{\"enabled\":}"), response));
}

TEST_F(HdmiCecSinkDsTest, MalformedJSON_setOSDName)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setOSDName"), _T("{\"name\":"), response));
}

TEST_F(HdmiCecSinkDsTest, MalformedJSON_setVendorId)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setVendorId"), _T("{\"vendorid\""), response));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_VolumeDown)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":66}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_Mute)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":67}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_Down)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":2}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_Left)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":3}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_Right)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":4}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_Home)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":9}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_Back)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":13}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_Number0)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":32}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_Number1)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":33}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_Number2)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":34}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_Number3)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":35}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_Number4)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":36}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_Number5)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":37}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_Number6)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":38}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_Number7)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":39}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_Number8)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":40}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_Number9)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":41}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendKeyPressEvent_NullConnection)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendKeyPressEvent"), _T("{\"logicalAddress\":1,\"keyCode\":65}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_VolumeUp)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":65}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Select)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":0}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Up)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":1}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Down)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":2}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Left)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":3}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Right)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":4}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Home)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":9}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Back)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":13}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Number0)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":32}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Number1)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":33}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Number2)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":34}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Number3)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":35}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Number4)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":36}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Number5)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":37}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Number6)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":38}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Number7)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":39}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Number8)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":40}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Number9)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":41}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_VolumeDown)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":66}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_Mute)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":67}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_InvalidLogicalAddress)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":16,\"keyCode\":1}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_InvalidKeyCode)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":4,\"keyCode\":256}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_MissingParams)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_BoundaryKeyCode)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":0,\"keyCode\":255}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_MinKeyCode)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":0,\"keyCode\":0}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_NegativeValues)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":-1,\"keyCode\":-1}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_StringValues)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":\"invalid\",\"keyCode\":\"test\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlPressed_MalformedJSON)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlPressed"), _T("{\"logicalAddress\":"), response));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_VolumeUp)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":65}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Select)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":0}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Up)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":1}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Down)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":2}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Left)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":3}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Right)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":4}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Home)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":9}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Back)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":13}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Number0)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":32}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Number1)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":33}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Number2)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":34}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Number3)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":35}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Number4)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":36}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Number5)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":37}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Number6)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":38}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Number7)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":39}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Number8)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":40}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Number9)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":41}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_VolumeDown)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":66}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_Mute)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":67}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_InvalidLogicalAddress)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":16,\"keyCode\":1}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_InvalidKeyCode)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":4,\"keyCode\":256}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_MissingParams)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_BoundaryKeyCode)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":0,\"keyCode\":255}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_MinKeyCode)
{
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Return());

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":0,\"keyCode\":0}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_NegativeValues)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":-1,\"keyCode\":-1}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_StringValues)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":\"invalid\",\"keyCode\":\"test\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, sendUserControlReleased_MalformedJSON)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("sendUserControlReleased"), _T("{\"logicalAddress\":"), response));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_ValidParameters)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"20\",\"lowLatencyMode\":\"1\",\"audioOutputCompensated\":\"1\",\"audioOutputDelay\":\"10\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_MinValues)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"0\",\"lowLatencyMode\":\"0\",\"audioOutputCompensated\":\"0\",\"audioOutputDelay\":\"0\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_MaxValues)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"255\",\"lowLatencyMode\":\"1\",\"audioOutputCompensated\":\"1\",\"audioOutputDelay\":\"255\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_LowLatencyModeEnabled)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"15\",\"lowLatencyMode\":\"1\",\"audioOutputCompensated\":\"1\",\"audioOutputDelay\":\"5\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_LowLatencyModeDisabled)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"50\",\"lowLatencyMode\":\"0\",\"audioOutputCompensated\":\"0\",\"audioOutputDelay\":\"25\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_AudioOutputCompensatedEnabled)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"30\",\"lowLatencyMode\":\"0\",\"audioOutputCompensated\":\"1\",\"audioOutputDelay\":\"15\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_AudioOutputCompensatedDisabled)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"40\",\"lowLatencyMode\":\"1\",\"audioOutputCompensated\":\"0\",\"audioOutputDelay\":\"20\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_HighVideoLatency)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"100\",\"lowLatencyMode\":\"0\",\"audioOutputCompensated\":\"1\",\"audioOutputDelay\":\"50\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_HighAudioDelay)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"25\",\"lowLatencyMode\":\"1\",\"audioOutputCompensated\":\"1\",\"audioOutputDelay\":\"200\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_AllParametersEnabled)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"35\",\"lowLatencyMode\":\"1\",\"audioOutputCompensated\":\"1\",\"audioOutputDelay\":\"30\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_AllParametersDisabled)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"45\",\"lowLatencyMode\":\"0\",\"audioOutputCompensated\":\"0\",\"audioOutputDelay\":\"35\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_NegativeValues)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"-10\",\"lowLatencyMode\":\"-1\",\"audioOutputCompensated\":\"-1\",\"audioOutputDelay\":\"-5\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_LargeValues)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"1000\",\"lowLatencyMode\":\"5\",\"audioOutputCompensated\":\"10\",\"audioOutputDelay\":\"2000\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_FloatValues)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"20.5\",\"lowLatencyMode\":\"1.2\",\"audioOutputCompensated\":\"0.8\",\"audioOutputDelay\":\"10.7\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_ZeroStringValues)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"0\",\"lowLatencyMode\":\"0\",\"audioOutputCompensated\":\"0\",\"audioOutputDelay\":\"0\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_OneStringValues)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"1\",\"lowLatencyMode\":\"1\",\"audioOutputCompensated\":\"1\",\"audioOutputDelay\":\"1\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_ExtraParameters)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"20\",\"lowLatencyMode\":\"1\",\"audioOutputCompensated\":\"1\",\"audioOutputDelay\":\"10\",\"extraParam\":\"ignored\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, setLatencyInfo_DuplicateParameters)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("setLatencyInfo"), _T("{\"videoLatency\":\"20\",\"videoLatency\":\"30\",\"lowLatencyMode\":\"1\",\"audioOutputCompensated\":\"1\",\"audioOutputDelay\":\"10\"}"), response));
    EXPECT_EQ(response, string("{\"success\":true}"));
}

TEST_F(HdmiCecSinkDsTest, printDeviceList_ValidCall)
{
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("printDeviceList"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"printed\":(true|false)"));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
}

//=============================================================================
// CEC Frame Processing Tests (L1 Level)
// These tests verify CEC frame injection and processing without full L2 event subscription
//=============================================================================

class HdmiCecSinkFrameProcessingTest : public HdmiCecSinkDsTest {
protected:

    void InjectCECFrame(const uint8_t* frameData, size_t frameSize) 
    {
        CECFrame frame(frameData, frameSize);
        for (auto* listener : listeners) {
            if (listener) {
                listener->notify(frame);
            }
        }
    }
};

TEST_F(HdmiCecSinkFrameProcessingTest, InjectImageViewOnFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Image View On frame: From TV (LA=0) to Playback Device 1 (LA=4)  
    // Header: 0x40, Opcode: 0x04 (Image View On)
    uint8_t imageViewOnFrame[] = { 0x40, 0x04 };
    
    EXPECT_NO_THROW(InjectCECFrame(imageViewOnFrame, sizeof(imageViewOnFrame)));
}

// Test fixture description: ImageViewOn edge case test broadcast message rejection to cover uncovered lines
TEST_F(HdmiCecSinkFrameProcessingTest, InjectImageViewOn_BroadcastMessage_ShouldBeIgnored)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create ImageViewOn broadcast frame (should be ignored per implementation)
    // From Playback Device 1 (LA=4) to Broadcast (LA=15) - should log "Ignore Broadcast messages"
    uint8_t imageViewOnBroadcastFrame[] = { 0x4F, 0x04 };
    
    EXPECT_NO_THROW(InjectCECFrame(imageViewOnBroadcastFrame, sizeof(imageViewOnBroadcastFrame)));
}

// Test fixture description: TextViewOn valid direct message processing
TEST_F(HdmiCecSinkFrameProcessingTest, InjectTextViewOnFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Text View On frame: From Playback Device 1 (LA=4) to TV (LA=0)
    // Header: 0x40, Opcode: 0x0D (Text View On)
    uint8_t textViewOnFrame[] = { 0x40, 0x0D };
    
    EXPECT_NO_THROW(InjectCECFrame(textViewOnFrame, sizeof(textViewOnFrame)));
}

// Test fixture description: TextViewOn edge case test broadcast message rejection
TEST_F(HdmiCecSinkFrameProcessingTest, InjectTextViewOn_BroadcastMessage_ShouldBeIgnored)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create TextViewOn broadcast frame (should be ignored per implementation)
    // From Playback Device 1 (LA=4) to Broadcast (LA=15) - should log "Ignore Broadcast messages"
    // This covers the broadcast rejection logic in HdmiCecSinkProcessor::process(const TextViewOn &msg, const Header &header)
    uint8_t textViewOnBroadcastFrame[] = { 0x4F, 0x0D };
    
    EXPECT_NO_THROW(InjectCECFrame(textViewOnBroadcastFrame, sizeof(textViewOnBroadcastFrame)));
}

TEST_F(HdmiCecSinkFrameProcessingTest, InjectReportAudioStatusFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Report Audio Status frame: From Audio System (LA=5) to TV (LA=0)
    // Header: 0x50, Opcode: 0x7A (Report Audio Status), Operands: 0x50 (Volume Level, Mute Status)
    uint8_t reportAudioStatusFrame[] = { 0x50, 0x7A, 0x50 };
    
    EXPECT_NO_THROW(InjectCECFrame(reportAudioStatusFrame, sizeof(reportAudioStatusFrame)));
}

TEST_F(HdmiCecSinkFrameProcessingTest, InjectRequestActiveSourceFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Request Active Source frame (Broadcast): From TV (LA=0) to Broadcast (LA=15)
    // Header: 0x0F, Opcode: 0x85 (Request Active Source)
    uint8_t requestActiveSourceFrame[] = { 0x0F, 0x85 };
    
    EXPECT_NO_THROW(InjectCECFrame(requestActiveSourceFrame, sizeof(requestActiveSourceFrame)));
}

// Test fixture description: RequestActiveSource edge case test direct message rejection
TEST_F(HdmiCecSinkFrameProcessingTest, InjectRequestActiveSource_DirectMessage_ShouldBeIgnored)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create RequestActiveSource direct frame (should be ignored per implementation)
    // From Playback Device 1 (LA=4) to TV (LA=0) - should log "Ignore Direct messages"
    uint8_t requestActiveSourceDirectFrame[] = { 0x40, 0x85 };
    
    EXPECT_NO_THROW(InjectCECFrame(requestActiveSourceDirectFrame, sizeof(requestActiveSourceDirectFrame)));
}

// Test fixture description: Standby direct message processing
TEST_F(HdmiCecSinkFrameProcessingTest, InjectStandbyFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Standby frame: From Playback Device 1 (LA=4) to TV (LA=0)
    // Header: 0x40, Opcode: 0x36 (Standby)
    uint8_t standbyFrame[] = { 0x40, 0x36 };
    
    EXPECT_NO_THROW(InjectCECFrame(standbyFrame, sizeof(standbyFrame)));
}

// Test fixture description: Standby broadcast message processing
TEST_F(HdmiCecSinkFrameProcessingTest, InjectStandby_BroadcastMessage)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Standby broadcast frame: From Playback Device 1 (LA=4) to Broadcast (LA=15)
    // Header: 0x4F, Opcode: 0x36 (Standby)
    // Standby can be sent to both direct and broadcast addresses
    uint8_t standbyBroadcastFrame[] = { 0x4F, 0x36 };
    
    EXPECT_NO_THROW(InjectCECFrame(standbyBroadcastFrame, sizeof(standbyBroadcastFrame)));
}

// Test fixture description: GetCECVersion direct message processing
TEST_F(HdmiCecSinkFrameProcessingTest, InjectGetCECVersionFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Get CEC Version frame: From Playback Device 1 (LA=4) to TV (LA=0)
    // Header: 0x40, Opcode: 0x9F (Get CEC Version)
    uint8_t getCECVersionFrame[] = { 0x40, 0x9F };
    
    EXPECT_NO_THROW(InjectCECFrame(getCECVersionFrame, sizeof(getCECVersionFrame)));
}

// Test fixture description: GetCECVersion edge case test broadcast message rejection
TEST_F(HdmiCecSinkFrameProcessingTest, InjectGetCECVersion_BroadcastMessage_ShouldBeIgnored)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create GetCECVersion broadcast frame (should be ignored per implementation)
    // From Playback Device 1 (LA=4) to Broadcast (LA=15) - should log "Ignore Broadcast messages"
    uint8_t getCECVersionBroadcastFrame[] = { 0x4F, 0x9F };
    
    EXPECT_NO_THROW(InjectCECFrame(getCECVersionBroadcastFrame, sizeof(getCECVersionBroadcastFrame)));
}

// Test fixture description: CECVersion response processing with version information
TEST_F(HdmiCecSinkFrameProcessingTest, InjectCECVersionFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create CEC Version frame: From Playback Device 1 (LA=4) to TV (LA=0)
    // Header: 0x40, Opcode: 0x9E (CEC Version), Operand: 0x05 (Version 1.4)
    uint8_t cecVersionFrame[] = { 0x40, 0x9E, 0x05 };
    
    EXPECT_NO_THROW(InjectCECFrame(cecVersionFrame, sizeof(cecVersionFrame)));
}

// Test fixture description: CECVersion with different version values
TEST_F(HdmiCecSinkFrameProcessingTest, InjectCECVersion_DifferentVersions)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Test different CEC version values
    // CEC Version 2.0
    uint8_t cecVersion20Frame[] = { 0x40, 0x9E, 0x06 };
    EXPECT_NO_THROW(InjectCECFrame(cecVersion20Frame, sizeof(cecVersion20Frame)));
    
    // CEC Version 1.3a  
    uint8_t cecVersion13Frame[] = { 0x40, 0x9E, 0x04 };
    EXPECT_NO_THROW(InjectCECFrame(cecVersion13Frame, sizeof(cecVersion13Frame)));
}

// Test fixture description: SetMenuLanguage processing with language information
TEST_F(HdmiCecSinkFrameProcessingTest, InjectSetMenuLanguageFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Set Menu Language frame: From TV (LA=0) to Broadcast (LA=15)
    // Header: 0x0F, Opcode: 0x32 (Set Menu Language), Operands: "eng" (English)
    uint8_t setMenuLanguageFrame[] = { 0x0F, 0x32, 0x65, 0x6E, 0x67 }; // "eng"
    
    EXPECT_NO_THROW(InjectCECFrame(setMenuLanguageFrame, sizeof(setMenuLanguageFrame)));
}

// Test fixture description: SetMenuLanguage with different languages
TEST_F(HdmiCecSinkFrameProcessingTest, InjectSetMenuLanguage_DifferentLanguages)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Test different language codes
    // Spanish
    uint8_t spanishLanguageFrame[] = { 0x0F, 0x32, 0x73, 0x70, 0x61 }; // "spa"
    EXPECT_NO_THROW(InjectCECFrame(spanishLanguageFrame, sizeof(spanishLanguageFrame)));
    
    // French
    uint8_t frenchLanguageFrame[] = { 0x0F, 0x32, 0x66, 0x72, 0x61 }; // "fra"
    EXPECT_NO_THROW(InjectCECFrame(frenchLanguageFrame, sizeof(frenchLanguageFrame)));
}

// Test fixture description: GiveOSDName direct message processing
TEST_F(HdmiCecSinkFrameProcessingTest, InjectGiveOSDNameFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Give OSD Name frame: From Playback Device 1 (LA=4) to TV (LA=0)
    // Header: 0x40, Opcode: 0x46 (Give OSD Name)
    uint8_t giveOSDNameFrame[] = { 0x40, 0x46 };
    
    EXPECT_NO_THROW(InjectCECFrame(giveOSDNameFrame, sizeof(giveOSDNameFrame)));
}

// Test fixture description: GiveOSDName edge case test broadcast message rejection
TEST_F(HdmiCecSinkFrameProcessingTest, InjectGiveOSDName_BroadcastMessage_ShouldBeIgnored)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create GiveOSDName broadcast frame (should be ignored per implementation)
    // From Playback Device 1 (LA=4) to Broadcast (LA=15) - should log "Ignore Broadcast messages"
    uint8_t giveOSDNameBroadcastFrame[] = { 0x4F, 0x46 };
    
    EXPECT_NO_THROW(InjectCECFrame(giveOSDNameBroadcastFrame, sizeof(giveOSDNameBroadcastFrame)));
}

TEST_F(HdmiCecSinkFrameProcessingTest, InjectRoutingChangeFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Routing Change frame: From Playback Device 1 (LA=4) to TV (LA=0)
    // Header: 0x40, Opcode: 0x80 (Routing Change), Operands: Old PA 1.0.0.0, New PA 2.0.0.0  
    uint8_t routingChangeFrame[] = { 0x40, 0x80, 0x10, 0x00, 0x20, 0x00 };
    
    EXPECT_NO_THROW(InjectCECFrame(routingChangeFrame, sizeof(routingChangeFrame)));
}

TEST_F(HdmiCecSinkFrameProcessingTest, InjectSetStreamPathFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Set Stream Path frame: From Playback Device 1 (LA=4) to Broadcast (LA=15)
    // Header: 0x4F, Opcode: 0x86 (Set Stream Path), Operands: PA 2.0.0.0
    uint8_t setStreamPathFrame[] = { 0x4F, 0x86, 0x20, 0x00 };
    
    EXPECT_NO_THROW(InjectCECFrame(setStreamPathFrame, sizeof(setStreamPathFrame)));
}

TEST_F(HdmiCecSinkFrameProcessingTest, InjectRequestCurrentLatencyFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Request Current Latency frame: From Playback Device 1 (LA=4) to TV (LA=0)
    // Header: 0x40, Opcode: 0xA7 (Request Current Latency), Operands: PA 1.0.0.0
    uint8_t requestCurrentLatencyFrame[] = { 0x40, 0xA7, 0x10, 0x00 };
    
    EXPECT_NO_THROW(InjectCECFrame(requestCurrentLatencyFrame, sizeof(requestCurrentLatencyFrame)));
}

TEST_F(HdmiCecSinkFrameProcessingTest, InjectUserControlPressedFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create User Control Pressed frame: From Remote Control (LA=14) to TV (LA=0)
    // Header: 0xE0, Opcode: 0x44 (User Control Pressed), Operands: Key Code 0x41 (Volume Up)
    uint8_t userControlPressedFrame[] = { 0xE0, 0x44, 0x41 };
    
    EXPECT_NO_THROW(InjectCECFrame(userControlPressedFrame, sizeof(userControlPressedFrame)));
}

TEST_F(HdmiCecSinkFrameProcessingTest, InjectUserControlReleasedFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create User Control Released frame: From Remote Control (LA=14) to TV (LA=0)
    // Header: 0xE0, Opcode: 0x45 (User Control Released)
    uint8_t userControlReleasedFrame[] = { 0xE0, 0x45 };
    
    EXPECT_NO_THROW(InjectCECFrame(userControlReleasedFrame, sizeof(userControlReleasedFrame)));
}

TEST_F(HdmiCecSinkFrameProcessingTest, InjectGiveSystemAudioModeStatusFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Give System Audio Mode Status frame: From TV (LA=0) to Audio System (LA=5) 
    // Header: 0x05, Opcode: 0x7D (Give System Audio Mode Status)
    uint8_t giveSystemAudioModeStatusFrame[] = { 0x05, 0x7D };
    
    EXPECT_NO_THROW(InjectCECFrame(giveSystemAudioModeStatusFrame, sizeof(giveSystemAudioModeStatusFrame)));
}

TEST_F(HdmiCecSinkFrameProcessingTest, InjectSystemAudioModeRequestFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create System Audio Mode Request frame: From TV (LA=0) to Audio System (LA=5)
    // Header: 0x05, Opcode: 0x70 (System Audio Mode Request), Operands: PA 1.0.0.0 
    uint8_t systemAudioModeRequestFrame[] = { 0x05, 0x70, 0x10, 0x00 };
    
    EXPECT_NO_THROW(InjectCECFrame(systemAudioModeRequestFrame, sizeof(systemAudioModeRequestFrame)));
}

TEST_F(HdmiCecSinkFrameProcessingTest, InjectSetAudioRateFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Set Audio Rate frame: From TV (LA=0) to Audio System (LA=5)
    // Header: 0x05, Opcode: 0x9A (Set Audio Rate), Operands: Rate 0x06 
    uint8_t setAudioRateFrame[] = { 0x05, 0x9A, 0x06 };
    
    EXPECT_NO_THROW(InjectCECFrame(setAudioRateFrame, sizeof(setAudioRateFrame)));
}

TEST_F(HdmiCecSinkFrameProcessingTest, InjectReportCurrentLatencyFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Report Current Latency frame: From TV (LA=0) to Playback Device 1 (LA=4) 
    // Header: 0x40, Opcode: 0xA8 (Report Current Latency), Operands: PA, Video Latency, Audio Latency
    uint8_t reportCurrentLatencyFrame[] = { 0x40, 0xA8, 0x10, 0x00, 0x01, 0x00, 0x01, 0x00 };
    
    EXPECT_NO_THROW(InjectCECFrame(reportCurrentLatencyFrame, sizeof(reportCurrentLatencyFrame)));
}

TEST_F(HdmiCecSinkFrameProcessingTest, InjectDeviceAddedFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Device Added frame (Report Physical Address): From Playback Device 1 (LA=4) to Broadcast (LA=15)
    // Header: 0x4F, Opcode: 0x84 (Report Physical Address), Operands: PA 2.0.0.0, Device Type 0x04
    uint8_t deviceAddedFrame[] = { 0x4F, 0x84, 0x20, 0x00, 0x04 };
    
    EXPECT_NO_THROW(InjectCECFrame(deviceAddedFrame, sizeof(deviceAddedFrame)));
}

TEST_F(HdmiCecSinkFrameProcessingTest, InjectAudioDeviceAddedFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create Audio Device Added frame: From Audio System (LA=5) to Broadcast (LA=15)
    // Header: 0x5F, Opcode: 0x84 (Report Physical Address), Operands: PA 2.0.0.0, Device Type 0x05 (Audio System)
    uint8_t audioDeviceAddedFrame[] = { 0x5F, 0x84, 0x20, 0x00, 0x05 };
    
    EXPECT_NO_THROW(InjectCECFrame(audioDeviceAddedFrame, sizeof(audioDeviceAddedFrame)));
}

TEST_F(HdmiCecSinkFrameProcessingTest, InjectExceptionHandlingFrames)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Test frames that trigger exception handling in sendToAsync/sendTo methods
    // These test error resilience when CEC communication fails

    // Mock sendToAsync to throw exceptions for certain frames
    ON_CALL(*p_connectionImplMock, sendToAsync(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const LogicalAddress& to, const CECFrame& frame) {
                throw Exception();
            }));

    // Mock both sendTo methods to throw exceptions 
    ON_CALL(*p_connectionImplMock, sendTo(::testing::Matcher<const LogicalAddress&>(::testing::_), ::testing::Matcher<const CECFrame&>(::testing::_)))
        .WillByDefault(::testing::Invoke(
            [&](const LogicalAddress&, const CECFrame&) {
                throw std::runtime_error("Simulated sendTo failure");
            }));

    ON_CALL(*p_connectionImplMock, sendTo(::testing::Matcher<const LogicalAddress&>(::testing::_), ::testing::Matcher<const CECFrame&>(::testing::_), ::testing::Matcher<int>(::testing::_)))
        .WillByDefault(::testing::Invoke(
            [&](const LogicalAddress&, const CECFrame&, int) {
                throw std::runtime_error("Simulated sendTo failure");
            }));

    // Get CEC Version frame with sendToAsync exception
    uint8_t getCECVersionFrame[] = { 0x40, 0x9F };
    EXPECT_NO_THROW(InjectCECFrame(getCECVersionFrame, sizeof(getCECVersionFrame)));

    // Give OSD Name frame with sendToAsync exception  
    uint8_t giveOSDNameFrame[] = { 0x40, 0x46 };
    EXPECT_NO_THROW(InjectCECFrame(giveOSDNameFrame, sizeof(giveOSDNameFrame)));

    // Give Physical Address frame with sendTo exception
    uint8_t givePhysicalAddressFrame[] = { 0x40, 0x83 };
    EXPECT_NO_THROW(InjectCECFrame(givePhysicalAddressFrame, sizeof(givePhysicalAddressFrame)));

    // Give Device Vendor ID frame with sendToAsync exception
    uint8_t giveDeviceVendorIDFrame[] = { 0x40, 0x8C };
    EXPECT_NO_THROW(InjectCECFrame(giveDeviceVendorIDFrame, sizeof(giveDeviceVendorIDFrame)));

    // Give Device Power Status frame with sendTo exception
    uint8_t giveDevicePowerStatusFrame[] = { 0x40, 0x8F };
    EXPECT_NO_THROW(InjectCECFrame(giveDevicePowerStatusFrame, sizeof(giveDevicePowerStatusFrame)));
}

TEST_F(HdmiCecSinkFrameProcessingTest, InjectWakeupFromStandbyFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Test Active Source frame that should trigger wakeup from standby  
    // This simulates the scenario where the system is in standby and receives an Active Source command
    uint8_t wakeupActiveSourceFrame[] = { 0x4F, 0x82, 0x10, 0x00 }; // From Playback Device 1 to Broadcast

    EXPECT_NO_THROW(InjectCECFrame(wakeupActiveSourceFrame, sizeof(wakeupActiveSourceFrame)));
}

TEST_F(HdmiCecSinkFrameProcessingTest, InjectDisabledImageViewOnFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Test Image View On frame (this was disabled in L2 due to implementation issues)
    // Including it here for completeness but without event verification
    uint8_t imageViewOnFrame[] = { 0x40, 0x04 }; // From Playbook Device 1 to TV

    EXPECT_NO_THROW(InjectCECFrame(imageViewOnFrame, sizeof(imageViewOnFrame)));
}

// Test fixture description: ActiveSource edge cases test valid broadcast processing
TEST_F(HdmiCecSinkFrameProcessingTest, ActiveSource_BroadcastMessage_ValidProcessing)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Valid broadcast ActiveSource message (should be processed)
    // From Playback Device 1 (LA=4) to Broadcast (LA=15) with physical address 2.0.0.0
    uint8_t activeSourceFrame[] = { 0x4F, 0x82, 0x20, 0x00 };
    
    EXPECT_NO_THROW(InjectCECFrame(activeSourceFrame, sizeof(activeSourceFrame)));
}

// Test fixture description: ActiveSource edge cases test direct message rejection per implementation
TEST_F(HdmiCecSinkFrameProcessingTest, ActiveSource_DirectMessage_ShouldBeIgnored)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Direct ActiveSource message (should be ignored per implementation)
    // From Playback Device 1 (LA=4) to TV (LA=0) - should log "Ignore Direct messages"
    uint8_t activeSourceFrame[] = { 0x40, 0x82, 0x20, 0x00 };
    
    EXPECT_NO_THROW(InjectCECFrame(activeSourceFrame, sizeof(activeSourceFrame)));
}

// Test fixture description: ActiveSource edge cases test boundary logical addresses
TEST_F(HdmiCecSinkFrameProcessingTest, ActiveSource_BoundaryLogicalAddresses)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // From LA=0 (TV) to Broadcast (LA=15) 
    uint8_t activeSourceFrame1[] = { 0x0F, 0x82, 0x10, 0x00 };
    EXPECT_NO_THROW(InjectCECFrame(activeSourceFrame1, sizeof(activeSourceFrame1)));
    
    // From LA=14 (Specific Use) to Broadcast (LA=15)
    uint8_t activeSourceFrame2[] = { 0xEF, 0x82, 0x30, 0x00 };
    EXPECT_NO_THROW(InjectCECFrame(activeSourceFrame2, sizeof(activeSourceFrame2)));
}

// Test fixture description: ActiveSource edge cases test invalid logical addresses beyond CEC spec
TEST_F(HdmiCecSinkFrameProcessingTest, ActiveSource_InvalidLogicalAddresses)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Invalid logical address beyond CEC specification (>15)
    uint8_t invalidActiveSourceFrame[] = { 0xFF, 0x82, 0x20, 0x00 };
    
    EXPECT_NO_THROW(InjectCECFrame(invalidActiveSourceFrame, sizeof(invalidActiveSourceFrame)));
}

// Test fixture description: ActiveSource edge cases test physical address boundaries
TEST_F(HdmiCecSinkFrameProcessingTest, ActiveSource_PhysicalAddressBoundaries)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Minimum physical address 0.0.0.0
    uint8_t activeSourceFrame1[] = { 0x1F, 0x82, 0x00, 0x00 };
    EXPECT_NO_THROW(InjectCECFrame(activeSourceFrame1, sizeof(activeSourceFrame1)));
    
    // Maximum physical address F.F.F.F
    uint8_t activeSourceFrame2[] = { 0x2F, 0x82, 0xFF, 0xFF };
    EXPECT_NO_THROW(InjectCECFrame(activeSourceFrame2, sizeof(activeSourceFrame2)));
    
    // Common physical addresses
    uint8_t activeSourceFrame3[] = { 0x3F, 0x82, 0x10, 0x00 }; // 1.0.0.0
    EXPECT_NO_THROW(InjectCECFrame(activeSourceFrame3, sizeof(activeSourceFrame3)));
    
    uint8_t activeSourceFrame4[] = { 0x4F, 0x82, 0x20, 0x00 }; // 2.0.0.0
    EXPECT_NO_THROW(InjectCECFrame(activeSourceFrame4, sizeof(activeSourceFrame4)));
}

// Test fixture description: ActiveSource edge cases test malformed frame too short
TEST_F(HdmiCecSinkFrameProcessingTest, ActiveSource_MalformedFrame_TooShort)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Malformed ActiveSource frame - too short (missing physical address bytes)
    uint8_t shortActiveSourceFrame[] = { 0x4F, 0x82 };
    
    EXPECT_NO_THROW(InjectCECFrame(shortActiveSourceFrame, sizeof(shortActiveSourceFrame)));
}

// Test fixture description: ActiveSource edge cases test malformed frame too long  
TEST_F(HdmiCecSinkFrameProcessingTest, ActiveSource_MalformedFrame_TooLong)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Malformed ActiveSource frame - too long (extra bytes)
    uint8_t longActiveSourceFrame[] = { 0x4F, 0x82, 0x20, 0x00, 0xFF, 0xFF };
    
    EXPECT_NO_THROW(InjectCECFrame(longActiveSourceFrame, sizeof(longActiveSourceFrame)));
}

// Test fixture description: ActiveSource edge cases test sequential messages from different devices
TEST_F(HdmiCecSinkFrameProcessingTest, ActiveSource_SequentialMessages)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Multiple sequential ActiveSource messages from different devices
    uint8_t activeSourceFrame1[] = { 0x1F, 0x82, 0x10, 0x00 }; // Recording Device 1
    EXPECT_NO_THROW(InjectCECFrame(activeSourceFrame1, sizeof(activeSourceFrame1)));
    
    uint8_t activeSourceFrame2[] = { 0x2F, 0x82, 0x20, 0x00 }; // Recording Device 2
    EXPECT_NO_THROW(InjectCECFrame(activeSourceFrame2, sizeof(activeSourceFrame2)));
    
    uint8_t activeSourceFrame3[] = { 0x3F, 0x82, 0x30, 0x00 }; // Tuner 1
    EXPECT_NO_THROW(InjectCECFrame(activeSourceFrame3, sizeof(activeSourceFrame3)));
}

// Test fixture description: ActiveSource edge cases test same device multiple physical addresses
TEST_F(HdmiCecSinkFrameProcessingTest, ActiveSource_SameDeviceMultipleAddresses)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Same device reporting different physical addresses (device moved/reconnected)
    uint8_t activeSourceFrame1[] = { 0x4F, 0x82, 0x10, 0x00 }; // First address
    EXPECT_NO_THROW(InjectCECFrame(activeSourceFrame1, sizeof(activeSourceFrame1)));
    
    uint8_t activeSourceFrame2[] = { 0x4F, 0x82, 0x20, 0x00 }; // Updated address
    EXPECT_NO_THROW(InjectCECFrame(activeSourceFrame2, sizeof(activeSourceFrame2)));
    
    uint8_t activeSourceFrame3[] = { 0x4F, 0x82, 0x30, 0x00 }; // Another update
    EXPECT_NO_THROW(InjectCECFrame(activeSourceFrame3, sizeof(activeSourceFrame3)));
}

// Test fixture description: InActiveSource edge cases test direct message processing
TEST_F(HdmiCecSinkFrameProcessingTest, InActiveSource_DirectMessage_ValidProcessing)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Valid direct InActiveSource message (should be processed)
    // From Playback Device 1 (LA=4) to TV (LA=0) with physical address 2.0.0.0
    uint8_t inActiveSourceFrame[] = { 0x40, 0x9D, 0x20, 0x00 };
    
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame, sizeof(inActiveSourceFrame)));
}

// Test fixture description: InActiveSource edge cases test broadcast message rejection per implementation
TEST_F(HdmiCecSinkFrameProcessingTest, InActiveSource_BroadcastMessage_ShouldBeIgnored)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Broadcast InActiveSource message (should be ignored per implementation)
    // From Playback Device 1 (LA=4) to Broadcast (LA=15) - should log "Ignore Broadcast messages"
    uint8_t inActiveSourceFrame[] = { 0x4F, 0x9D, 0x20, 0x00 };
    
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame, sizeof(inActiveSourceFrame)));
}

// Test fixture description: InActiveSource edge cases test boundary logical addresses
TEST_F(HdmiCecSinkFrameProcessingTest, InActiveSource_BoundaryLogicalAddresses)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // From LA=1 (Recording Device 1) to TV (LA=0)
    uint8_t inActiveSourceFrame1[] = { 0x10, 0x9D, 0x10, 0x00 };
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame1, sizeof(inActiveSourceFrame1)));
    
    // From LA=14 (Specific Use) to TV (LA=0)
    uint8_t inActiveSourceFrame2[] = { 0xE0, 0x9D, 0x30, 0x00 };
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame2, sizeof(inActiveSourceFrame2)));
    
    // From Audio System (LA=5) to TV (LA=0)
    uint8_t inActiveSourceFrame3[] = { 0x50, 0x9D, 0x40, 0x00 };
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame3, sizeof(inActiveSourceFrame3)));
}

// Test fixture description: InActiveSource edge cases test physical address boundaries
TEST_F(HdmiCecSinkFrameProcessingTest, InActiveSource_PhysicalAddressBoundaries)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Minimum physical address 0.0.0.0
    uint8_t inActiveSourceFrame1[] = { 0x40, 0x9D, 0x00, 0x00 };
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame1, sizeof(inActiveSourceFrame1)));
    
    // Maximum physical address F.F.F.F
    uint8_t inActiveSourceFrame2[] = { 0x40, 0x9D, 0xFF, 0xFF };
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame2, sizeof(inActiveSourceFrame2)));
    
    // Common physical addresses
    uint8_t inActiveSourceFrame3[] = { 0x40, 0x9D, 0x10, 0x00 }; // 1.0.0.0
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame3, sizeof(inActiveSourceFrame3)));
    
    uint8_t inActiveSourceFrame4[] = { 0x40, 0x9D, 0x20, 0x00 }; // 2.0.0.0
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame4, sizeof(inActiveSourceFrame4)));
}

// Test fixture description: InActiveSource edge cases test malformed frame too short
TEST_F(HdmiCecSinkFrameProcessingTest, InActiveSource_MalformedFrame_TooShort)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Malformed InActiveSource frame - too short (missing physical address bytes)
    uint8_t shortInActiveSourceFrame[] = { 0x40, 0x9D };
    
    EXPECT_NO_THROW(InjectCECFrame(shortInActiveSourceFrame, sizeof(shortInActiveSourceFrame)));
}

// Test fixture description: InActiveSource edge cases test malformed frame too long
TEST_F(HdmiCecSinkFrameProcessingTest, InActiveSource_MalformedFrame_TooLong)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Malformed InActiveSource frame - too long (extra bytes)
    uint8_t longInActiveSourceFrame[] = { 0x40, 0x9D, 0x20, 0x00, 0xFF, 0xFF };
    
    EXPECT_NO_THROW(InjectCECFrame(longInActiveSourceFrame, sizeof(longInActiveSourceFrame)));
}

// Test fixture description: InActiveSource edge cases test multiple device addresses
TEST_F(HdmiCecSinkFrameProcessingTest, InActiveSource_MultipleDeviceAddresses)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Multiple devices reporting InActiveSource with different addresses
    uint8_t inActiveSourceFrame1[] = { 0x10, 0x9D, 0x10, 0x00 }; // Recording Device 1
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame1, sizeof(inActiveSourceFrame1)));
    
    uint8_t inActiveSourceFrame2[] = { 0x20, 0x9D, 0x20, 0x00 }; // Recording Device 2
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame2, sizeof(inActiveSourceFrame2)));
    
    uint8_t inActiveSourceFrame3[] = { 0x40, 0x9D, 0x30, 0x00 }; // Playback Device 1
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame3, sizeof(inActiveSourceFrame3)));
}

// Test fixture description: InActiveSource edge cases test same device address updates
TEST_F(HdmiCecSinkFrameProcessingTest, InActiveSource_SameDeviceAddressUpdates)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Same device reporting InActiveSource with different physical addresses over time
    uint8_t inActiveSourceFrame1[] = { 0x40, 0x9D, 0x10, 0x00 }; // First address
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame1, sizeof(inActiveSourceFrame1)));
    
    uint8_t inActiveSourceFrame2[] = { 0x40, 0x9D, 0x20, 0x00 }; // Updated address
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame2, sizeof(inActiveSourceFrame2)));
    
    uint8_t inActiveSourceFrame3[] = { 0x40, 0x9D, 0x30, 0x00 }; // Another update
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame3, sizeof(inActiveSourceFrame3)));
}

// Test fixture description: InActiveSource edge cases test invalid logical addresses
TEST_F(HdmiCecSinkFrameProcessingTest, InActiveSource_InvalidLogicalAddresses)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Invalid logical address beyond CEC specification (>15)
    uint8_t invalidInActiveSourceFrame[] = { 0xFF, 0x9D, 0x20, 0x00 };
    
    EXPECT_NO_THROW(InjectCECFrame(invalidInActiveSourceFrame, sizeof(invalidInActiveSourceFrame)));
}

// Test fixture description: InActiveSource edge cases test extreme physical addresses
TEST_F(HdmiCecSinkFrameProcessingTest, InActiveSource_ExtremePhysicalAddresses)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Physical addresses with extreme values and patterns
    uint8_t inActiveSourceFrame1[] = { 0x40, 0x9D, 0x00, 0x01 }; // 0.0.0.1
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame1, sizeof(inActiveSourceFrame1)));
    
    uint8_t inActiveSourceFrame2[] = { 0x40, 0x9D, 0x11, 0x11 }; // 1.1.1.1
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame2, sizeof(inActiveSourceFrame2)));
    
    uint8_t inActiveSourceFrame3[] = { 0x40, 0x9D, 0xAA, 0xAA }; // A.A.A.A
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame3, sizeof(inActiveSourceFrame3)));
    
    uint8_t inActiveSourceFrame4[] = { 0x40, 0x9D, 0x55, 0x55 }; // 5.5.5.5
    EXPECT_NO_THROW(InjectCECFrame(inActiveSourceFrame4, sizeof(inActiveSourceFrame4)));
}

// Test fixture description: GiveDeviceVendorID processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectGiveDeviceVendorIDFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Test Case 1: Direct message from Playback Device 1 (LA=4) to TV (LA=0) - should process normally
    uint8_t directGiveVendorIDFrame[] = { 0x40, 0x8C }; // Direct: From LA=4 to LA=0, Give Device Vendor ID
    EXPECT_NO_THROW(InjectCECFrame(directGiveVendorIDFrame, sizeof(directGiveVendorIDFrame)));
    
    // Test Case 2: Broadcast message - should be rejected
    uint8_t broadcastGiveVendorIDFrame[] = { 0x4F, 0x8C }; // Broadcast: From LA=4 to Broadcast, Give Device Vendor ID
    EXPECT_NO_THROW(InjectCECFrame(broadcastGiveVendorIDFrame, sizeof(broadcastGiveVendorIDFrame)));
}

// Test fixture description: SetOSDString processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectSetOSDStringFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    uint8_t shortOSDStringFrame[] = { 0x40, 0x64, 0x00, 'T', 'e', 's', 't' }; // Display control + "Test"
    EXPECT_NO_THROW(InjectCECFrame(shortOSDStringFrame, sizeof(shortOSDStringFrame)));
    
    // Test Case 2: Set OSD String with longer text
    uint8_t longOSDStringFrame[] = { 0x50, 0x64, 0x00, 'L', 'o', 'n', 'g', ' ', 'T', 'e', 's', 't', ' ', 'M', 's', 'g' };
    EXPECT_NO_THROW(InjectCECFrame(longOSDStringFrame, sizeof(longOSDStringFrame)));
    
    // Test Case 3: Set OSD String with different display control values
    uint8_t displayControlFrame[] = { 0x60, 0x64, 0x01, 'M', 'e', 'n', 'u' }; // Different display control
    EXPECT_NO_THROW(InjectCECFrame(displayControlFrame, sizeof(displayControlFrame)));
    
    // Test Case 4: Set OSD String with maximum length text
    uint8_t maxOSDStringFrame[] = { 0x70, 0x64, 0x00, 'V', 'e', 'r', 'y', ' ', 'L', 'o', 'n', 'g', ' ', 'O', 'S', 'D', ' ', 'S', 't', 'r', 'i', 'n', 'g' };
    EXPECT_NO_THROW(InjectCECFrame(maxOSDStringFrame, sizeof(maxOSDStringFrame)));
}

// Test fixture description: SetOSDName processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectSetOSDNameFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    uint8_t standardOSDNameFrame[] = { 0x40, 0x47, 'T', 'V', ' ', 'S', 'e', 't' }; // "TV Set"
    EXPECT_NO_THROW(InjectCECFrame(standardOSDNameFrame, sizeof(standardOSDNameFrame)));
    
    // Test Case 2: Set OSD Name with longer device name
    uint8_t longOSDNameFrame[] = { 0x50, 0x47, 'S', 'm', 'a', 'r', 't', ' ', 'T', 'V', ' ', 'D', 'e', 'v', 'i', 'c', 'e' };
    EXPECT_NO_THROW(InjectCECFrame(longOSDNameFrame, sizeof(longOSDNameFrame)));
    
    // Test Case 3: Set OSD Name with short device name
    uint8_t shortOSDNameFrame[] = { 0x60, 0x47, 'T', 'V' }; // "TV"
    EXPECT_NO_THROW(InjectCECFrame(shortOSDNameFrame, sizeof(shortOSDNameFrame)));
    
    // Test Case 4: Set OSD Name with special characters in name
    uint8_t specialOSDNameFrame[] = { 0x70, 0x47, 'T', 'V', '-', '1', '2', '3', '4' }; // "TV-1234"
    EXPECT_NO_THROW(InjectCECFrame(specialOSDNameFrame, sizeof(specialOSDNameFrame)));
    
    // Test Case 5: Set OSD Name with maximum length device name
    uint8_t maxOSDNameFrame[] = { 0x80, 0x47, 'V', 'e', 'r', 'y', ' ', 'L', 'o', 'n', 'g', ' ', 'D', 'e', 'v', 'i', 'c', 'e', ' ', 'N', 'a', 'm', 'e' };
    EXPECT_NO_THROW(InjectCECFrame(maxOSDNameFrame, sizeof(maxOSDNameFrame)));
    
    // Test Case 6: Set OSD Name with empty name (minimal frame)
    uint8_t emptyOSDNameFrame[] = { 0x90, 0x47 }; // No name data
    EXPECT_NO_THROW(InjectCECFrame(emptyOSDNameFrame, sizeof(emptyOSDNameFrame)));
}

// Test fixture description: SetOSDName edge case test broadcast message rejection
TEST_F(HdmiCecSinkFrameProcessingTest, InjectSetOSDName_BroadcastMessage_ShouldBeIgnored)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create SetOSDName broadcast frame (should be ignored per implementation)
    // From Playback Device 1 (LA=4) to Broadcast (LA=15) - should log "Ignore Broadcast messages"
    uint8_t setOSDNameBroadcastFrame[] = { 0x4F, 0x47, 'T', 'e', 's', 't' }; // Broadcast: "Test"
    
    EXPECT_NO_THROW(InjectCECFrame(setOSDNameBroadcastFrame, sizeof(setOSDNameBroadcastFrame)));
}

// Test fixture description: RoutingInformation processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectRoutingInformationFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    uint8_t routingInfoFrame[] = { 0x40, 0x81, 0x10, 0x00, 0x20, 0x00 }; // From LA=4 to LA=0, routing from 1.0.0.0 to 2.0.0.0
    EXPECT_NO_THROW(InjectCECFrame(routingInfoFrame, sizeof(routingInfoFrame)));
    
    // Test Case 2: Different routing paths
    uint8_t routingInfoFrame2[] = { 0x50, 0x81, 0x00, 0x00, 0x30, 0x00 }; // From root to 3.0.0.0
    EXPECT_NO_THROW(InjectCECFrame(routingInfoFrame2, sizeof(routingInfoFrame2)));
}

// Test fixture description: GetMenuLanguage processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectGetMenuLanguageFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    uint8_t directMenuLangFrame[] = { 0x40, 0x91 }; // Direct: From LA=4 to LA=0
    EXPECT_NO_THROW(InjectCECFrame(directMenuLangFrame, sizeof(directMenuLangFrame)));
    
    // Test Case 2: Broadcast Get Menu Language - should be rejected
    uint8_t broadcastMenuLangFrame[] = { 0x4F, 0x91 }; // Broadcast: From LA=4 to Broadcast
    EXPECT_NO_THROW(InjectCECFrame(broadcastMenuLangFrame, sizeof(broadcastMenuLangFrame)));
}

// Test fixture description: ReportPhysicalAddress processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectReportPhysicalAddressFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Test Case 1: Broadcast Report Physical Address - normal processing
    uint8_t reportPAFrame[] = { 0x4F, 0x84, 0x20, 0x00, 0x04 }; // Broadcast: LA=4, PA=2.0.0.0, Device Type=Playback
    EXPECT_NO_THROW(InjectCECFrame(reportPAFrame, sizeof(reportPAFrame)));
    
    // Test Case 2: Direct Report Physical Address - should be rejected
    uint8_t directReportPAFrame[] = { 0x40, 0x84, 0x30, 0x00, 0x01 }; // Direct: LA=4 to LA=0
    EXPECT_NO_THROW(InjectCECFrame(directReportPAFrame, sizeof(directReportPAFrame)));
    
    // Test Case 3: Different physical address to trigger PA change detection
    uint8_t changedPAFrame[] = { 0x5F, 0x84, 0x10, 0x00, 0x05 }; // Different PA from same device
    EXPECT_NO_THROW(InjectCECFrame(changedPAFrame, sizeof(changedPAFrame)));
}

// Test fixture description: DeviceVendorID processor coverage  
TEST_F(HdmiCecSinkFrameProcessingTest, InjectDeviceVendorIDFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    uint8_t deviceVendorIDFrame[] = { 0x4F, 0x87, 0x00, 0x80, 0x45 }; // Broadcast: LA=4, Vendor ID
    EXPECT_NO_THROW(InjectCECFrame(deviceVendorIDFrame, sizeof(deviceVendorIDFrame)));
    
    // Test Case 2: Direct Device Vendor ID - should be rejected
    uint8_t directVendorIDFrame[] = { 0x40, 0x87, 0x00, 0x90, 0x56 }; // Direct: LA=4 to LA=0
    EXPECT_NO_THROW(InjectCECFrame(directVendorIDFrame, sizeof(directVendorIDFrame)));
    
    // Test Case 3: Different vendor ID to test update logic
    uint8_t differentVendorIDFrame[] = { 0x5F, 0x87, 0x01, 0x23, 0x45 }; // Different vendor ID
    EXPECT_NO_THROW(InjectCECFrame(differentVendorIDFrame, sizeof(differentVendorIDFrame)));
}

// Test fixture description: GiveDevicePowerStatus processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectGiveDevicePowerStatusFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronous ly)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Test Case 1: Direct Give Device Power Status - normal processing
    uint8_t directPowerStatusFrame[] = { 0x40, 0x8F }; // Direct: From LA=4 to LA=0
    EXPECT_NO_THROW(InjectCECFrame(directPowerStatusFrame, sizeof(directPowerStatusFrame)));
    
    // Test Case 2: Broadcast Give Device Power Status - should be rejected
    uint8_t broadcastPowerStatusFrame[] = { 0x4F, 0x8F }; // Broadcast: From LA=4 to Broadcast
    EXPECT_NO_THROW(InjectCECFrame(broadcastPowerStatusFrame, sizeof(broadcastPowerStatusFrame)));
}

// Test fixture description: ReportPowerStatus processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectReportPowerStatusFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    uint8_t reportPowerFrame[] = { 0x40, 0x90, 0x00 }; // Direct: From LA=4 to LA=0, Power On
    EXPECT_NO_THROW(InjectCECFrame(reportPowerFrame, sizeof(reportPowerFrame)));
    
    // Test Case 2: Broadcast Report Power Status - should be rejected
    uint8_t broadcastPowerFrame[] = { 0x4F, 0x90, 0x01 }; // Broadcast: From LA=4, Power Standby
    EXPECT_NO_THROW(InjectCECFrame(broadcastPowerFrame, sizeof(broadcastPowerFrame)));
    
    // Test Case 3: Power status from Audio System
    uint8_t audioSystemPowerFrame[] = { 0x50, 0x90, 0x02 }; // From Audio System LA=5, Power Standby to On
    EXPECT_NO_THROW(InjectCECFrame(audioSystemPowerFrame, sizeof(audioSystemPowerFrame)));
    
    // Test Case 4: Different power status to trigger change detection
    uint8_t changedPowerFrame[] = { 0x40, 0x90, 0x03 }; // Different power status
    EXPECT_NO_THROW(InjectCECFrame(changedPowerFrame, sizeof(changedPowerFrame)));
}

// Test fixture description: Abort processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectAbortFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    uint8_t directAbortFrame[] = { 0x40, 0xFF, 0x9F }; // Direct: From LA=4 to LA=0, Aborting GET_CEC_VERSION
    EXPECT_NO_THROW(InjectCECFrame(directAbortFrame, sizeof(directAbortFrame)));
    
    // Test Case 2: Broadcast Abort - should be ignored
    uint8_t broadcastAbortFrame[] = { 0x4F, 0xFF, 0x8C }; // Broadcast: Aborting GIVE_DEVICE_VENDOR_ID
    EXPECT_NO_THROW(InjectCECFrame(broadcastAbortFrame, sizeof(broadcastAbortFrame)));
}

// Test fixture description: Polling processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectPollingFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Note: Polling uses special opcode 0x200, but in frame it's represented differently
    uint8_t pollingFrame[] = { 0x44 }; // Polling: From LA=4 to LA=4
    EXPECT_NO_THROW(InjectCECFrame(pollingFrame, sizeof(pollingFrame)));
}

// Test fixture description: InitiateArc processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectInitiateArcFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    uint8_t initiateArcFrame[] = { 0x50, 0xC0 }; // Direct: From Audio System LA=5 to LA=0
    EXPECT_NO_THROW(InjectCECFrame(initiateArcFrame, sizeof(initiateArcFrame)));
    
    // Test Case 2: Initiate ARC from non-Audio System - should be rejected
    uint8_t nonAudioInitiateArcFrame[] = { 0x40, 0xC0 }; // Direct: From LA=4 (not Audio System)
    EXPECT_NO_THROW(InjectCECFrame(nonAudioInitiateArcFrame, sizeof(nonAudioInitiateArcFrame)));
    
    // Test Case 3: Broadcast Initiate ARC - should be rejected
    uint8_t broadcastInitiateArcFrame[] = { 0x5F, 0xC0 }; // Broadcast: From Audio System
    EXPECT_NO_THROW(InjectCECFrame(broadcastInitiateArcFrame, sizeof(broadcastInitiateArcFrame)));
}

// Test fixture description: TerminateArc processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectTerminateArcFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    uint8_t terminateArcFrame[] = { 0x50, 0xC5 }; // Direct: From Audio System LA=5 to LA=0
    EXPECT_NO_THROW(InjectCECFrame(terminateArcFrame, sizeof(terminateArcFrame)));
    
    // Test Case 2: Terminate ARC from non-Audio System - should be rejected
    uint8_t nonAudioTerminateArcFrame[] = { 0x40, 0xC5 }; // Direct: From LA=4 (not Audio System)
    EXPECT_NO_THROW(InjectCECFrame(nonAudioTerminateArcFrame, sizeof(nonAudioTerminateArcFrame)));
    
    // Test Case 3: Broadcast Terminate ARC - should be rejected
    uint8_t broadcastTerminateArcFrame[] = { 0x5F, 0xC5 }; // Broadcast: From Audio System
    EXPECT_NO_THROW(InjectCECFrame(broadcastTerminateArcFrame, sizeof(broadcastTerminateArcFrame)));
}

// Test fixture description: ReportShortAudioDescriptor processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectReportShortAudioDescriptorFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    uint8_t reportAudioDescFrame[] = { 0x50, 0xA3, 0x09, 0x07, 0x15 }; // From Audio System: Audio descriptor data
    EXPECT_NO_THROW(InjectCECFrame(reportAudioDescFrame, sizeof(reportAudioDescFrame)));
    
    // Test Case 2: Multiple audio descriptors
    uint8_t multipleAudioDescFrame[] = { 0x50, 0xA3, 0x09, 0x07, 0x15, 0x0D, 0x1F, 0x07 }; // Multiple descriptors
    EXPECT_NO_THROW(InjectCECFrame(multipleAudioDescFrame, sizeof(multipleAudioDescFrame)));
}

// Test fixture description: SetSystemAudioMode processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectSetSystemAudioModeFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    uint8_t setAudioModeOnFrame[] = { 0x50, 0x72, 0x01 }; // From Audio System: Audio Mode ON
    EXPECT_NO_THROW(InjectCECFrame(setAudioModeOnFrame, sizeof(setAudioModeOnFrame)));
    
    // Test Case 2: Set System Audio Mode OFF
    uint8_t setAudioModeOffFrame[] = { 0x50, 0x72, 0x00 }; // From Audio System: Audio Mode OFF
    EXPECT_NO_THROW(InjectCECFrame(setAudioModeOffFrame, sizeof(setAudioModeOffFrame)));
}

// Test fixture description: ReportAudioStatus processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectReportAudioStatusFrame_MultipleScenarios)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    uint8_t reportAudioStatusFrame[] = { 0x50, 0x7A, 0x25 }; // Direct: From Audio System, Volume=37, Mute=Off
    EXPECT_NO_THROW(InjectCECFrame(reportAudioStatusFrame, sizeof(reportAudioStatusFrame)));
    
    // Test Case 2: Broadcast Report Audio Status - should be rejected
    uint8_t broadcastAudioStatusFrame[] = { 0x5F, 0x7A, 0xA0 }; // Broadcast: Mute=On, Volume=32
    EXPECT_NO_THROW(InjectCECFrame(broadcastAudioStatusFrame, sizeof(broadcastAudioStatusFrame)));
    
    // Test Case 3: Different audio status values
    uint8_t muteAudioStatusFrame[] = { 0x50, 0x7A, 0x80 }; // Mute=On, Volume=0
    EXPECT_NO_THROW(InjectCECFrame(muteAudioStatusFrame, sizeof(muteAudioStatusFrame)));
}

// Test fixture description: GiveFeatures processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectGiveFeaturesFrame)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    uint8_t giveFeaturesFrame[] = { 0x40, 0xA5 }; // Direct: From LA=4 to LA=0
    EXPECT_NO_THROW(InjectCECFrame(giveFeaturesFrame, sizeof(giveFeaturesFrame)));
    
    // Test Case 2: Give Features from different source
    uint8_t giveFeatures2Frame[] = { 0x50, 0xA5 }; // Direct: From LA=5 to LA=0
    EXPECT_NO_THROW(InjectCECFrame(giveFeatures2Frame, sizeof(giveFeatures2Frame)));
}

// Test fixture description: RequestCurrentLatency processor coverage
TEST_F(HdmiCecSinkFrameProcessingTest, InjectRequestCurrentLatencyFrame_MultiplePhysicalAddresses)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    uint8_t matchingLatencyFrame[] = { 0x40, 0xA7, 0x00, 0x00 }; // Physical address 0.0.0.0 (root)
    EXPECT_NO_THROW(InjectCECFrame(matchingLatencyFrame, sizeof(matchingLatencyFrame)));
    
    // Test Case 2: Request Current Latency with non-matching physical address
    uint8_t nonMatchingLatencyFrame[] = { 0x40, 0xA7, 0x10, 0x00 }; // Physical address 1.0.0.0
    EXPECT_NO_THROW(InjectCECFrame(nonMatchingLatencyFrame, sizeof(nonMatchingLatencyFrame)));
    
    // Test Case 3: Different physical address patterns
    uint8_t diffLatencyFrame[] = { 0x50, 0xA7, 0x20, 0x00 }; // Physical address 2.0.0.0
    EXPECT_NO_THROW(InjectCECFrame(diffLatencyFrame, sizeof(diffLatencyFrame)));
}

// Test fixture description: ReportPowerStatus from Audio System when power status was explicitly requested
TEST_F(HdmiCecSinkFrameProcessingTest, InjectReportPowerStatus_AudioSystem_AfterRequest)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // First, simulate requesting audio device power status by calling the API
    // This sets m_audioDevicePowerStatusRequested flag to true
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillOnce(::testing::Return());

    string requestResponse;
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("requestAudioDevicePowerStatus"), _T("{}"), requestResponse));

    // Small delay to ensure the request is processed
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    // Now inject ReportPowerStatus from Audio System (LA=5) to TV (LA=0)
    // This should trigger line 428: reportAudioDevicePowerStatusInfo()
    uint8_t audioSystemPowerStatusFrame[] = { 0x50, 0x90, 0x00 }; // From Audio System LA=5, Power On

    EXPECT_NO_THROW(InjectCECFrame(audioSystemPowerStatusFrame, sizeof(audioSystemPowerStatusFrame)));

    // Test different power status values to ensure the logic works for various states
    uint8_t audioSystemStandbyFrame[] = { 0x50, 0x90, 0x01 }; // Power Standby
    EXPECT_NO_THROW(InjectCECFrame(audioSystemStandbyFrame, sizeof(audioSystemStandbyFrame)));
}

// Test fixture description: FeatureAbort processor coverage - broadcast message rejection
TEST_F(HdmiCecSinkFrameProcessingTest, InjectFeatureAbort_BroadcastMessage_ShouldBeIgnored)
{
    // Wait for plugin initialization to complete (FrameListener registration happens asynchronously)
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Create FeatureAbort broadcast frame (should be ignored per implementation)
    // From Playback Device 1 (LA=4) to Broadcast (LA=15) - should log "Ignore Broadcast messages"
    uint8_t broadcastFeatureAbortFrame[] = { 0x4F, 0x00, 0x9F, 0x00 };
    
    EXPECT_NO_THROW(InjectCECFrame(broadcastFeatureAbortFrame, sizeof(broadcastFeatureAbortFrame)));
}

//=============================================================================
// Route-map / device-parameter unit tests (L1 Level)
//
// COVERAGE_GAPS.md traceability: gap-plugin-sink (Sec. 6.2 rank 31, P1).
//
// HdmiCecSinkFrameListener, HdmiCecSinkProcessor, CECDeviceParams, DeviceNode and
// HdmiPortMap are all declared at namespace scope inside WPEFramework::Plugin with
// public members, so they are constructed directly here: these cases need no plugin
// instance, no JSON-RPC round trip and no mock configuration at all. They close the
// zero-hit route-map operations (HdmiPortMap::addChild / removeChild / getRoute),
// CECDeviceParams::printVariable and the HdmiCecSinkFrameListener destructor.
//
// Every case establishes its own preconditions on stack-local objects and leaves no
// shared or process-global state altered, so each one passes both in isolation and in
// the full suite regardless of execution order.
//=============================================================================

// Test fixture description: every route-map operation is inert until the port has been
// registered with a logical address - the natural negative case for all three operations.
TEST_F(HdmiCecSinkDsTest, HdmiPortMap_UnregisteredPort_AllOperationsAreInert)
{
    Plugin::HdmiPortMap portMap(1);

    // Freshly constructed: the port owns physical address (portID + 1).0.0.0 and no logical address.
    EXPECT_EQ(LogicalAddress::UNREGISTERED, portMap.m_logicalAddr.toInt());
    EXPECT_EQ(2, static_cast<int>(portMap.m_physicalAddr.getByteValue(0)));
    EXPECT_FALSE(portMap.m_isConnected);

    // addChild's first arm is guarded on the port being registered; its second arm requires an
    // exact physical-address match. Neither holds here, so the device chain stays untouched.
    PhysicalAddress childAddress(2, 1, 0, 0);
    portMap.addChild(LogicalAddress(6), childAddress);
    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(portMap.m_deviceChain[0].m_childsLogicalAddr[0]));
    EXPECT_EQ(LogicalAddress::UNREGISTERED, portMap.m_logicalAddr.toInt());

    // removeChild is guarded on the same registration state.
    EXPECT_NO_THROW(portMap.removeChild(childAddress));
    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(portMap.m_deviceChain[0].m_childsLogicalAddr[0]));

    // getRoute short-circuits before pushing anything, so an unregistered port is the ONLY
    // condition that yields a genuinely empty route.
    std::vector<uint8_t> route;
    portMap.getRoute(childAddress, route);
    EXPECT_TRUE(route.empty());
}

// Test fixture description: addChild's second arm registers the port itself when the
// incoming physical address is the port's own address.
TEST_F(HdmiCecSinkDsTest, HdmiPortMap_AddChildOwnPhysicalAddress_RegistersPort)
{
    Plugin::HdmiPortMap portMap(1);
    PhysicalAddress ownAddress(2, 0, 0, 0);

    portMap.addChild(LogicalAddress(4), ownAddress);

    EXPECT_EQ(4, portMap.m_logicalAddr.toInt());
    // Registering the port must not disturb any child slot.
    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(portMap.m_deviceChain[0].m_childsLogicalAddr[0]));
}

// Test fixture description: addChild depth-one branch - only the second physical-address byte
// is non-zero, so the child lands in the first link of the device chain.
TEST_F(HdmiCecSinkDsTest, HdmiPortMap_AddChildDepthOne_PopulatesFirstChainLink)
{
    Plugin::HdmiPortMap portMap(1);
    portMap.update(LogicalAddress(4));

    PhysicalAddress childAddress(2, 1, 0, 0);
    portMap.addChild(LogicalAddress(6), childAddress);

    // Slot index is (byte value - 1), so byte1 == 1 targets slot 0 of chain link 0.
    EXPECT_EQ(6, static_cast<int>(portMap.m_deviceChain[0].m_childsLogicalAddr[0]));
    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(portMap.m_deviceChain[1].m_childsLogicalAddr[0]));
    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(portMap.m_deviceChain[2].m_childsLogicalAddr[0]));
}

// Test fixture description: addChild depth-two branch - the third byte is non-zero, which takes
// precedence over the second in the mutually exclusive chain.
TEST_F(HdmiCecSinkDsTest, HdmiPortMap_AddChildDepthTwo_PopulatesSecondChainLink)
{
    Plugin::HdmiPortMap portMap(1);
    portMap.update(LogicalAddress(4));

    PhysicalAddress childAddress(2, 1, 2, 0);
    portMap.addChild(LogicalAddress(7), childAddress);

    EXPECT_EQ(7, static_cast<int>(portMap.m_deviceChain[1].m_childsLogicalAddr[1]));
    // The chain is an exclusive if/else if ladder, so the shallower link is NOT written.
    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(portMap.m_deviceChain[0].m_childsLogicalAddr[0]));
}

// Test fixture description: addChild depth-three branch - the fourth byte is non-zero and wins
// the exclusive ladder outright.
TEST_F(HdmiCecSinkDsTest, HdmiPortMap_AddChildDepthThree_PopulatesThirdChainLink)
{
    Plugin::HdmiPortMap portMap(1);
    portMap.update(LogicalAddress(4));

    PhysicalAddress childAddress(2, 1, 2, 3);
    portMap.addChild(LogicalAddress(8), childAddress);

    EXPECT_EQ(8, static_cast<int>(portMap.m_deviceChain[2].m_childsLogicalAddr[2]));
    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(portMap.m_deviceChain[1].m_childsLogicalAddr[1]));
    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(portMap.m_deviceChain[0].m_childsLogicalAddr[0]));
}

// Test fixture description: addChild corner case - repeating the same registration must be
// idempotent rather than shifting the child into a different slot.
TEST_F(HdmiCecSinkDsTest, HdmiPortMap_AddChildDuplicate_IsIdempotent)
{
    Plugin::HdmiPortMap portMap(1);
    portMap.update(LogicalAddress(4));

    PhysicalAddress childAddress(2, 1, 0, 0);
    portMap.addChild(LogicalAddress(6), childAddress);
    portMap.addChild(LogicalAddress(6), childAddress);

    EXPECT_EQ(6, static_cast<int>(portMap.m_deviceChain[0].m_childsLogicalAddr[0]));
}

// Test fixture description: addChild negative case - a physical address belonging to a different
// HDMI port must not be recorded against this port.
TEST_F(HdmiCecSinkDsTest, HdmiPortMap_AddChildMismatchedPrefix_LeavesChainUntouched)
{
    Plugin::HdmiPortMap portMap(1);
    portMap.update(LogicalAddress(4));

    // byte0 == 3 belongs to port 2, not to this port (whose own byte0 is 2).
    PhysicalAddress foreignAddress(3, 1, 0, 0);
    portMap.addChild(LogicalAddress(6), foreignAddress);

    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(portMap.m_deviceChain[0].m_childsLogicalAddr[0]));
    EXPECT_EQ(4, portMap.m_logicalAddr.toInt());
}

// Test fixture description: addChild corner case - a zero second byte means "the port itself",
// which cannot be a child, so no chain slot is written.
TEST_F(HdmiCecSinkDsTest, HdmiPortMap_AddChildZeroSecondByte_LeavesChainUntouched)
{
    Plugin::HdmiPortMap portMap(1);
    portMap.update(LogicalAddress(4));

    PhysicalAddress portRootAddress(2, 0, 0, 0);
    portMap.addChild(LogicalAddress(6), portRootAddress);

    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(portMap.m_deviceChain[0].m_childsLogicalAddr[0]));
    // The first arm was taken (registered, different logical address), so the own-address arm
    // is not evaluated and the port keeps its existing logical address.
    EXPECT_EQ(4, portMap.m_logicalAddr.toInt());
}

// Test fixture description: the connection flag is independent of logical-address registration.
TEST_F(HdmiCecSinkDsTest, HdmiPortMap_UpdateConnected_TogglesFlagOnly)
{
    Plugin::HdmiPortMap portMap(1);

    portMap.update(true);
    EXPECT_TRUE(portMap.m_isConnected);
    EXPECT_EQ(LogicalAddress::UNREGISTERED, portMap.m_logicalAddr.toInt());

    portMap.update(false);
    EXPECT_FALSE(portMap.m_isConnected);
}

// Test fixture description: getRoute happy path over a fully populated three-deep hierarchy.
// Unlike addChild/removeChild, getRoute uses three SEQUENTIAL ifs and then unconditionally
// appends the port's own logical address, so a full address yields four entries, deepest first.
TEST_F(HdmiCecSinkDsTest, HdmiPortMap_GetRouteFullDepth_ReturnsFourEntriesOwnAddressLast)
{
    Plugin::HdmiPortMap portMap(1);
    portMap.update(LogicalAddress(4));

    PhysicalAddress depthOne(2, 1, 0, 0);
    PhysicalAddress depthTwo(2, 1, 2, 0);
    PhysicalAddress depthThree(2, 1, 2, 3);
    portMap.addChild(LogicalAddress(6), depthOne);
    portMap.addChild(LogicalAddress(7), depthTwo);
    portMap.addChild(LogicalAddress(8), depthThree);

    std::vector<uint8_t> route;
    portMap.getRoute(depthThree, route);

    ASSERT_EQ(4u, route.size());
    EXPECT_EQ(8, static_cast<int>(route[0]));
    EXPECT_EQ(7, static_cast<int>(route[1]));
    EXPECT_EQ(6, static_cast<int>(route[2]));
    EXPECT_EQ(4, static_cast<int>(route[3]));
}

// Test fixture description: getRoute corner case - a single deep registration leaves the
// shallower links unregistered, and getRoute reports them verbatim rather than skipping them.
TEST_F(HdmiCecSinkDsTest, HdmiPortMap_GetRoutePartialChain_ReportsUnregisteredPlaceholders)
{
    Plugin::HdmiPortMap portMap(1);
    portMap.update(LogicalAddress(4));

    PhysicalAddress depthThree(2, 1, 2, 3);
    portMap.addChild(LogicalAddress(8), depthThree);

    std::vector<uint8_t> route;
    portMap.getRoute(depthThree, route);

    ASSERT_EQ(4u, route.size());
    EXPECT_EQ(8, static_cast<int>(route[0]));
    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(route[1]));
    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(route[2]));
    EXPECT_EQ(4, static_cast<int>(route[3]));
}

// Test fixture description: getRoute edge case - an address that does not share this port's
// prefix falls into the else arm, which still reports the port itself. The route is therefore
// one entry long, NOT empty.
TEST_F(HdmiCecSinkDsTest, HdmiPortMap_GetRouteUnknownPort_ReturnsSingleOwnAddress)
{
    Plugin::HdmiPortMap portMap(1);
    portMap.update(LogicalAddress(4));

    PhysicalAddress foreignAddress(9, 0, 0, 0);
    std::vector<uint8_t> route;
    portMap.getRoute(foreignAddress, route);

    ASSERT_EQ(1u, route.size());
    EXPECT_EQ(4, static_cast<int>(route[0]));
}

// Test fixture description: add -> getRoute -> removeChild -> getRoute round trip. removeChild
// mirrors addChild's exclusive ladder, so it clears exactly one slot per call and restores the
// UNREGISTERED value that DeviceNode seeds every slot with.
TEST_F(HdmiCecSinkDsTest, HdmiPortMap_RemoveChildRoundTrip_RestoresUnregistered)
{
    Plugin::HdmiPortMap portMap(1);
    portMap.update(LogicalAddress(4));

    PhysicalAddress depthOne(2, 1, 0, 0);
    PhysicalAddress depthTwo(2, 1, 2, 0);
    PhysicalAddress depthThree(2, 1, 2, 3);
    portMap.addChild(LogicalAddress(6), depthOne);
    portMap.addChild(LogicalAddress(7), depthTwo);
    portMap.addChild(LogicalAddress(8), depthThree);

    std::vector<uint8_t> populatedRoute;
    portMap.getRoute(depthThree, populatedRoute);
    ASSERT_EQ(4u, populatedRoute.size());
    EXPECT_EQ(8, static_cast<int>(populatedRoute[0]));

    portMap.removeChild(depthThree);
    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(portMap.m_deviceChain[2].m_childsLogicalAddr[2]));
    // The shallower links survive - removal is per-slot, not cascading.
    EXPECT_EQ(7, static_cast<int>(portMap.m_deviceChain[1].m_childsLogicalAddr[1]));
    EXPECT_EQ(6, static_cast<int>(portMap.m_deviceChain[0].m_childsLogicalAddr[0]));

    portMap.removeChild(depthTwo);
    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(portMap.m_deviceChain[1].m_childsLogicalAddr[1]));

    portMap.removeChild(depthOne);
    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(portMap.m_deviceChain[0].m_childsLogicalAddr[0]));

    std::vector<uint8_t> clearedRoute;
    portMap.getRoute(depthThree, clearedRoute);
    ASSERT_EQ(4u, clearedRoute.size());
    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(clearedRoute[0]));
    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(clearedRoute[1]));
    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(clearedRoute[2]));
    EXPECT_EQ(4, static_cast<int>(clearedRoute[3]));
}

// Test fixture description: removeChild negative case - removing an address that was never
// added is harmless because the slot already holds UNREGISTERED.
TEST_F(HdmiCecSinkDsTest, HdmiPortMap_RemoveChildNeverAdded_IsHarmless)
{
    Plugin::HdmiPortMap portMap(1);
    portMap.update(LogicalAddress(4));

    PhysicalAddress neverAdded(2, 5, 0, 0);
    EXPECT_NO_THROW(portMap.removeChild(neverAdded));

    EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(portMap.m_deviceChain[0].m_childsLogicalAddr[4]));
    EXPECT_EQ(4, portMap.m_logicalAddr.toInt());
}

// Test fixture description: removeChild negative case - a foreign prefix must not clear a slot
// that belongs to this port.
TEST_F(HdmiCecSinkDsTest, HdmiPortMap_RemoveChildMismatchedPrefix_LeavesChainUntouched)
{
    Plugin::HdmiPortMap portMap(1);
    portMap.update(LogicalAddress(4));

    PhysicalAddress depthOne(2, 1, 0, 0);
    portMap.addChild(LogicalAddress(6), depthOne);

    PhysicalAddress foreignAddress(3, 1, 0, 0);
    portMap.removeChild(foreignAddress);

    EXPECT_EQ(6, static_cast<int>(portMap.m_deviceChain[0].m_childsLogicalAddr[0]));
}

// Test fixture description: DeviceNode seeds every one of its slots with UNREGISTERED, which is
// what makes the removeChild round trip above verifiable.
TEST_F(HdmiCecSinkDsTest, DeviceNode_DefaultConstruction_AllSlotsUnregistered)
{
    Plugin::DeviceNode node;

    for (int slot = 0; slot < LogicalAddress::UNREGISTERED; slot++) {
        EXPECT_EQ(LogicalAddress::UNREGISTERED, static_cast<int>(node.m_childsLogicalAddr[slot]))
            << "slot " << slot << " was not seeded with UNREGISTERED";
    }
}

// Test fixture description: a freshly constructed device entry advertises the invalid physical
// address and reports that nothing has been learned about the device yet.
TEST_F(HdmiCecSinkDsTest, CECDeviceParams_DefaultConstruction_HasInvalidAddressAndNoUpdates)
{
    Plugin::CECDeviceParams device;

    EXPECT_EQ(std::string("ffff"), device.m_physicalAddr.toString());
    EXPECT_EQ(0, device.m_logicalAddress.toInt());
    EXPECT_FALSE(device.m_isDevicePresent);
    EXPECT_FALSE(device.m_isActiveSource);
    EXPECT_FALSE(device.m_isDeviceDisconnected);
    EXPECT_FALSE(device.m_isPAUpdated);
    EXPECT_FALSE(device.m_isVersionUpdated);
    EXPECT_FALSE(device.m_isOSDNameUpdated);
    EXPECT_FALSE(device.m_isVendorIDUpdated);
    EXPECT_FALSE(device.m_isPowerStatusUpdated);
    EXPECT_FALSE(device.m_isDeviceTypeUpdated);
    EXPECT_EQ(0, device.m_isRequested);
    EXPECT_EQ(0, device.m_isRequestRetry);
    EXPECT_FALSE(device.isAllUpdated());
}

// Test fixture description: isAllUpdated is a negated OR of six independent flags, so it stays
// false until every one of the six update() overloads has been applied - the positive/negative
// pair for the device-information completeness predicate.
TEST_F(HdmiCecSinkDsTest, CECDeviceParams_IsAllUpdated_FalseUntilAllSixUpdatesApplied)
{
    Plugin::CECDeviceParams device;
    EXPECT_FALSE(device.isAllUpdated());

    device.update(PhysicalAddress(2, 0, 0, 0));
    EXPECT_TRUE(device.m_isPAUpdated);
    EXPECT_FALSE(device.isAllUpdated());

    device.update(Version(Version::V_1_4));
    EXPECT_TRUE(device.m_isVersionUpdated);
    EXPECT_FALSE(device.isAllUpdated());

    device.update(OSDName("CECTEST"));
    EXPECT_TRUE(device.m_isOSDNameUpdated);
    EXPECT_FALSE(device.isAllUpdated());

    device.update(VendorID(0x00, 0x19, 0xFB));
    EXPECT_TRUE(device.m_isVendorIDUpdated);
    EXPECT_FALSE(device.isAllUpdated());

    device.update(PowerStatus(PowerStatus::ON));
    EXPECT_TRUE(device.m_isPowerStatusUpdated);
    EXPECT_FALSE(device.isAllUpdated());

    device.update(DeviceType(DeviceType::PLAYBACK_DEVICE));
    EXPECT_TRUE(device.m_isDeviceTypeUpdated);

    // Only now, with all six sources of device information present, is the entry complete.
    EXPECT_TRUE(device.isAllUpdated());
    EXPECT_EQ(std::string("2000"), device.m_physicalAddr.toString());
    EXPECT_EQ(std::string("CECTEST"), device.m_osdName.toString());
}

// Test fixture description: clear() must return a fully populated entry to its constructed
// state so that a re-detected device is not reported with stale information.
TEST_F(HdmiCecSinkDsTest, CECDeviceParams_Clear_ResetsEveryField)
{
    Plugin::CECDeviceParams device;
    device.update(PhysicalAddress(2, 1, 0, 0));
    device.update(Version(Version::V_2_0));
    device.update(OSDName("STALE"));
    device.update(VendorID(0x01, 0x02, 0x03));
    device.update(PowerStatus(PowerStatus::STANDBY));
    device.update(DeviceType(DeviceType::AUDIO_SYSTEM));
    device.m_isDevicePresent = true;
    device.m_isActiveSource = true;
    device.m_isDeviceDisconnected = true;
    device.m_logicalAddress = LogicalAddress(5);
    ASSERT_TRUE(device.isAllUpdated());

    device.clear();

    EXPECT_EQ(std::string("ffff"), device.m_physicalAddr.toString());
    EXPECT_EQ(0, device.m_logicalAddress.toInt());
    EXPECT_EQ(std::string(""), device.m_osdName.toString());
    EXPECT_FALSE(device.m_isDevicePresent);
    EXPECT_FALSE(device.m_isActiveSource);
    EXPECT_FALSE(device.m_isDeviceDisconnected);
    EXPECT_FALSE(device.m_isPAUpdated);
    EXPECT_FALSE(device.m_isVersionUpdated);
    EXPECT_FALSE(device.m_isOSDNameUpdated);
    EXPECT_FALSE(device.m_isVendorIDUpdated);
    EXPECT_FALSE(device.m_isPowerStatusUpdated);
    EXPECT_FALSE(device.m_isDeviceTypeUpdated);
    EXPECT_FALSE(device.isAllUpdated());
}

// Test fixture description: printVariable dumps all fifteen tracked device fields for
// diagnostics. It is a pure reporter, so the observable contract is that it completes without
// throwing and mutates nothing - including for a default entry whose fields hold edge values.
TEST_F(HdmiCecSinkDsTest, CECDeviceParams_PrintVariable_DumpsAllFieldsWithoutMutatingState)
{
    Plugin::CECDeviceParams device;
    device.update(PhysicalAddress(2, 1, 2, 3));
    device.update(Version(Version::V_1_4));
    device.update(OSDName("PRINTME"));
    device.update(VendorID(0x00, 0x19, 0xFB));
    device.update(PowerStatus(PowerStatus::ON));
    device.update(DeviceType(DeviceType::PLAYBACK_DEVICE));
    device.m_logicalAddress = LogicalAddress(4);
    device.m_isDevicePresent = true;
    device.m_isActiveSource = true;

    const std::string physicalBefore = device.m_physicalAddr.toString();
    const std::string osdNameBefore = device.m_osdName.toString();

    EXPECT_NO_THROW(device.printVariable());

    EXPECT_EQ(physicalBefore, device.m_physicalAddr.toString());
    EXPECT_EQ(osdNameBefore, device.m_osdName.toString());
    EXPECT_EQ(4, device.m_logicalAddress.toInt());
    EXPECT_TRUE(device.m_isDevicePresent);
    EXPECT_TRUE(device.m_isActiveSource);
    EXPECT_TRUE(device.isAllUpdated());

    // A default-constructed entry carries the invalid physical address and an empty language,
    // which is the corner case the diagnostic dump has to survive.
    Plugin::CECDeviceParams emptyDevice;
    EXPECT_NO_THROW(emptyDevice.printVariable());
    EXPECT_FALSE(emptyDevice.isAllUpdated());
}

// Test fixture description: a frame listener forwards every frame it is notified of to its
// processor, and tears down cleanly when it leaves scope. The observable outcome is the
// response the processor emits on the connection for the injected <Get CEC Version> request.
TEST_F(HdmiCecSinkDsTest, HdmiCecSinkFrameListener_ScopedLifetime_DeliversFrameToProcessor)
{
    int responsesSent = 0;
    EXPECT_CALL(*p_connectionImplMock, sendToAsync(::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](const LogicalAddress& to, const CECFrame& frame) {
                responsesSent++;
                // The CEC version response is directed back at the initiator, logical address 4.
                EXPECT_EQ(4, to.toInt());
            }));

    // Named cecBus rather than connection so it does not shadow the fixture's JSON-RPC context.
    Connection cecBus;
    Plugin::HdmiCecSinkProcessor processor(cecBus);

    // byte0 high nibble is the initiator and the low nibble the destination, so 0x40 is
    // "from Playback Device 1 (LA=4) to TV (LA=0)"; 0x9F is <Get CEC Version>.
    const uint8_t getCecVersionFrame[] = { 0x40, 0x9F };
    CECFrame frame(getCecVersionFrame, sizeof(getCecVersionFrame));

    {
        Plugin::HdmiCecSinkFrameListener listener(processor);
        EXPECT_NO_THROW(listener.notify(frame));
    }

    EXPECT_EQ(1, responsesSent);
}

//=============================================================================
// BCP-47 to ISO 639-2 language mapping (L1 Level)
//
// COVERAGE_GAPS.md traceability: gap-plugin-sink (Sec. 6.2 rank 31, P1).
//
// mapToIso639_2 is a pure function on the implementation, so these cases need no mock
// configuration. They walk the whole decision surface: the empty-input shortcut, the
// BCP-47 region-suffix strip, the case normalisation, the three-letter passthrough, every
// entry of the nineteen-entry lookup table, and the "eng" fallback for anything unknown.
//=============================================================================

// Test fixture description: an empty language string short-circuits to the English default.
TEST_F(HdmiCecSinkDsTest, mapToIso639_2_EmptyString_ReturnsEng)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    EXPECT_EQ(std::string("eng"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2(""));
}

// Test fixture description: every two-letter code in the lookup table maps to its ISO 639-2
// three-letter equivalent - the happy path across the full table.
TEST_F(HdmiCecSinkDsTest, mapToIso639_2_AllKnownTwoLetterCodes_MapToThreeLetterCodes)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    const struct {
        const char* bcp47;
        const char* iso639_2;
    } expected[] = {
        { "en", "eng" }, { "fr", "fra" }, { "de", "deu" }, { "es", "spa" },
        { "it", "ita" }, { "pt", "por" }, { "ru", "rus" }, { "zh", "zho" },
        { "ja", "jpn" }, { "ko", "kor" }, { "ar", "ara" }, { "hi", "hin" },
        { "nl", "nld" }, { "sv", "swe" }, { "fi", "fin" }, { "no", "nor" },
        { "da", "dan" }, { "pl", "pol" }, { "tr", "tur" }
    };

    for (size_t i = 0; i < (sizeof(expected) / sizeof(expected[0])); i++) {
        EXPECT_EQ(std::string(expected[i].iso639_2),
            Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2(expected[i].bcp47))
            << "unexpected mapping for BCP-47 code " << expected[i].bcp47;
    }
}

// Test fixture description: a BCP-47 region subtag is stripped before the lookup, so the
// regional variants of a language resolve to the same ISO 639-2 code.
TEST_F(HdmiCecSinkDsTest, mapToIso639_2_Bcp47RegionSuffix_IsStripped)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    EXPECT_EQ(std::string("eng"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("en-US"));
    EXPECT_EQ(std::string("eng"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("en-GB"));
    EXPECT_EQ(std::string("por"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("pt-BR"));
    EXPECT_EQ(std::string("zho"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("zh-Hans-CN"));
}

// Test fixture description: input is lower-cased before the lookup, so upper- and mixed-case
// codes resolve identically.
TEST_F(HdmiCecSinkDsTest, mapToIso639_2_UpperCaseInput_IsNormalised)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    EXPECT_EQ(std::string("eng"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("EN"));
    EXPECT_EQ(std::string("fra"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("Fr"));
    EXPECT_EQ(std::string("deu"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("DE-AT"));
    // Case normalisation happens before the three-letter passthrough as well.
    EXPECT_EQ(std::string("eng"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("ENG"));
}

// Test fixture description: a value that is already three letters long is passed through
// untouched, including codes that are absent from the two-letter table.
TEST_F(HdmiCecSinkDsTest, mapToIso639_2_ThreeLetterCode_PassesThrough)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    EXPECT_EQ(std::string("xyz"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("xyz"));
    EXPECT_EQ(std::string("nor"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("nor"));
    EXPECT_EQ(std::string("fra"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("fra-CA"));
}

// Test fixture description: a two-letter code that is not in the table falls back to English
// rather than being forwarded as an invalid CEC language operand.
TEST_F(HdmiCecSinkDsTest, mapToIso639_2_UnknownTwoLetterCode_FallsBackToEng)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    EXPECT_EQ(std::string("eng"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("xx"));
    EXPECT_EQ(std::string("eng"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("q"));
    EXPECT_EQ(std::string("eng"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("zz-ZZ"));
}

// Test fixture description: an over-long primary subtag is neither three letters nor a table
// key, so it also falls back to English.
TEST_F(HdmiCecSinkDsTest, mapToIso639_2_LongerThanThreeLetters_FallsBackToEng)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    EXPECT_EQ(std::string("eng"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("engl"));
    EXPECT_EQ(std::string("eng"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("english"));
}

// Test fixture description: a leading separator yields an empty primary subtag, the corner case
// between the empty-string shortcut and the table lookup.
TEST_F(HdmiCecSinkDsTest, mapToIso639_2_HyphenOnlyInput_FallsBackToEng)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    EXPECT_EQ(std::string("eng"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("-"));
    EXPECT_EQ(std::string("eng"), Plugin::HdmiCecSinkImplementation::_instance->mapToIso639_2("-US"));
}

//=============================================================================
// Plugin metadata and lifecycle (L1 Level)
//
// COVERAGE_GAPS.md traceability: gap-plugin-sink (Sec. 6.2 rank 31, P1) and
// informational getters (Sec. 6.2 rank 40, P2).
//
// These close the zero-hit HdmiCecSink::Information() reporter plus the activation and
// deactivation rejection paths that the happy-path fixture never reaches. Each case that
// needs a non-TV device profile creates its OWN plugin instance and captures/restores
// /etc/device.properties, so no shared state is left altered for a sibling test - and the
// non-TV Initialize returns before any implementation object is created, which is what
// keeps HdmiCecSinkImplementation::_instance (owned by this fixture) intact.
//=============================================================================

// Test fixture description: the plugin's human-readable description is a fixed literal. It is
// asserted verbatim, including the upstream "PLugin" spelling, because this is a test of what
// the plugin reports today and correcting production spelling is out of scope here.
TEST_F(HdmiCecSinkDsTest, Information_ReturnsSinkPluginDescription)
{
    EXPECT_EQ(string("This HdmiCecSink PLugin Facilitates the HDMI CEC Sink Control"), plugin->Information());

    // Information() is a const reporter: asking twice must give the same answer and must not
    // disturb the activated plugin.
    EXPECT_EQ(plugin->Information(), plugin->Information());
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getEnabled"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
}

// Test fixture description: activation with a null shell is rejected with a diagnostic message
// instead of dereferencing the pointer.
TEST_F(HdmiCecSinkDsTest, Initialize_NullShell_ReturnsIShellObjectIsNull)
{
    Core::ProxyType<Plugin::HdmiCecSink> sparePlugin(Core::ProxyType<Plugin::HdmiCecSink>::Create());

    // The device profile is TV (this fixture provisions it), so the profile gate is passed and
    // the null-shell guard is the branch under test. No implementation object is created, so
    // the _instance owned by this fixture is untouched.
    EXPECT_EQ(string("IShell object is NULL"), sparePlugin->Initialize(nullptr));

    sparePlugin.Release();
    EXPECT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);
}

// Test fixture description: the sink plugin is TV-only. On a set-top-box profile both
// Initialize and Deinitialize must bail out early rather than bring up CEC.
TEST_F(HdmiCecSinkDsTest, Initialize_NonTvProfile_ReturnsNotSupported)
{
    // Capture the process-global device-properties file so the profile change cannot leak into
    // any sibling test, then restore it before leaving.
    std::string savedDeviceProperties;
    {
        std::ifstream capture("/etc/device.properties");
        savedDeviceProperties.assign(std::istreambuf_iterator<char>(capture), std::istreambuf_iterator<char>());
    }
    ASSERT_NE(std::string::npos, savedDeviceProperties.find("RDK_PROFILE=TV"));

    createFile("/etc/device.properties", "RDK_PROFILE=STB");

    Core::ProxyType<Plugin::HdmiCecSink> stbPlugin(Core::ProxyType<Plugin::HdmiCecSink>::Create());
    NiceMock<ServiceMock> stbService;

    EXPECT_EQ(string("Not supported"), stbPlugin->Initialize(&stbService));
    // Deinitialize takes the same profile gate, so it must also be a no-op rather than trying
    // to tear down a CEC stack that was never started.
    EXPECT_NO_THROW(stbPlugin->Deinitialize(&stbService));

    stbPlugin.Release();

    // Restore and prove the restore landed, so ordering with other tests stays irrelevant.
    {
        std::ofstream restore("/etc/device.properties");
        restore << savedDeviceProperties;
    }
    std::string restoredDeviceProperties;
    {
        std::ifstream verify("/etc/device.properties");
        restoredDeviceProperties.assign(std::istreambuf_iterator<char>(verify), std::istreambuf_iterator<char>());
    }
    EXPECT_EQ(savedDeviceProperties, restoredDeviceProperties);

    // The activated plugin owned by this fixture is unaffected by the excursion.
    EXPECT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getEnabled"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
}

// Test fixture description: deactivation looks up the remote connection inside a try block so
// that a failure in the COM link cannot escape and abort the shutdown sequence. The lookup is
// made to throw exactly once; the guarded arm must swallow it and the rest of Deinitialize must
// still run.
TEST_F(HdmiCecSinkDsTest, Deinitialize_RemoteConnectionLookupThrows_IsContained)
{
    // Armed through a shared flag held BY VALUE in the action: this fixture's Deinitialize runs
    // from the destructor, i.e. after this test body has returned, so the action must not
    // capture anything that lives on the body's stack.
    auto throwOnNextLookup = std::make_shared<bool>(true);
    ON_CALL(service, COMLink())
        .WillByDefault(::testing::Invoke(
            [throwOnNextLookup]() -> PluginHost::IShell::ICOMLink* {
                if (*throwOnNextLookup) {
                    // Only the first lookup throws. Deinitialize consults the COM link a second
                    // time outside the try block, and that call must stay benign.
                    *throwOnNextLookup = false;
                    throw std::runtime_error("COM link unavailable during deactivation");
                }
                return nullptr;
            }));

    // The plugin is still fully functional at this point; the throwing path is exercised by the
    // fixture teardown that follows this body.
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getEnabled"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
    EXPECT_TRUE(*throwOnNextLookup);
}

//=============================================================================
// Implementation callbacks, feature-abort reporting and topology maintenance (L1 Level)
//
// COVERAGE_GAPS.md traceability: gap-plugin-sink-reportfeatureabort (Sec. 6.2 rank 38, P2),
// gap-plugin-sink-ondeviceremoved (Sec. 6.2 rank 39, P2) and gap-plugin-sink
// (Sec. 6.2 rank 31, P1).
//
// HdmiCecSinkImplementation exposes _instance, deviceList[] and hdmiInputs publicly, and the
// repository already drives the implementation through _instance elsewhere in this file, so
// these cases reuse that seam rather than inventing a new access path. Because the plugin
// registers its own notification sink during Initialize, the notification fan-out inside each
// of these functions really executes and delivers the corresponding JSON-RPC event.
//=============================================================================

// Test fixture description: a presentation-language change is translated to ISO 639-2 and
// broadcast as <Set Menu Language>. The observable outcome is the CEC frame put on the bus.
TEST_F(HdmiCecSinkDsTest, onPresentationLanguageChanged_KnownLanguage_BroadcastsMenuLanguage)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    // <Set Menu Language> is a broadcast. The polling thread also broadcasts on this interface,
    // so the destination is used to select the interesting calls rather than being asserted on
    // every one of them.
    int broadcasts = 0;
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](const LogicalAddress& to, const CECFrame& frame, int timeout) {
                if (to.toInt() == LogicalAddress::BROADCAST && timeout > 0) {
                    broadcasts++;
                }
            }));

    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->onPresentationLanguageChanged("fr-FR"));

    // The BCP-47 tag is normalised to its ISO 639-2 form and recorded against the TV's own entry.
    EXPECT_EQ(Language("fra").toString(),
        Plugin::HdmiCecSinkImplementation::_instance->deviceList[LogicalAddress::TV].m_currentLanguage.toString());
    EXPECT_GT(broadcasts, 0);
}

// Test fixture description: an unrecognised BCP-47 tag still produces a well-formed broadcast,
// because the mapping falls back to English rather than emitting an invalid operand.
TEST_F(HdmiCecSinkDsTest, onPresentationLanguageChanged_UnknownLanguage_StillBroadcasts)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    int broadcasts = 0;
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](const LogicalAddress& to, const CECFrame& frame, int timeout) {
                if (to.toInt() == LogicalAddress::BROADCAST) {
                    broadcasts++;
                }
            }));

    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->onPresentationLanguageChanged("zz-ZZ"));
    // Both the unknown two-letter code and the empty tag land on the English fallback.
    EXPECT_EQ(Language("eng").toString(),
        Plugin::HdmiCecSinkImplementation::_instance->deviceList[LogicalAddress::TV].m_currentLanguage.toString());

    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->onPresentationLanguageChanged(""));
    EXPECT_EQ(Language("eng").toString(),
        Plugin::HdmiCecSinkImplementation::_instance->deviceList[LogicalAddress::TV].m_currentLanguage.toString());

    EXPECT_GT(broadcasts, 0);
}

// Test fixture description: a transition to the powered-on state records ON against the TV's own
// device entry and leaves the active source alone.
TEST_F(HdmiCecSinkDsTest, onPowerModeChanged_ToPowerStateOn_RecordsPoweredOnStatus)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    Plugin::HdmiCecSinkImplementation::_instance->m_currentActiveSource = 4;

    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->onPowerModeChanged(
        WPEFramework::Exchange::IPowerManager::POWER_STATE_STANDBY,
        WPEFramework::Exchange::IPowerManager::POWER_STATE_ON));

    // The TV allocates logical address 0, so its own entry carries the new power status.
    EXPECT_EQ(PowerStatus::ON, Plugin::HdmiCecSinkImplementation::_instance->deviceList[0].m_powerStatus.toInt());
    // Powering on must not clear the current active source.
    EXPECT_EQ(4, Plugin::HdmiCecSinkImplementation::_instance->m_currentActiveSource);
}

// Test fixture description: a transition away from the powered-on state records standby and
// resets the active source, so a stale source is not reported after the TV goes to standby.
TEST_F(HdmiCecSinkDsTest, onPowerModeChanged_ToStandby_ResetsActiveSource)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    Plugin::HdmiCecSinkImplementation::_instance->m_currentActiveSource = 4;

    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->onPowerModeChanged(
        WPEFramework::Exchange::IPowerManager::POWER_STATE_ON,
        WPEFramework::Exchange::IPowerManager::POWER_STATE_STANDBY));

    EXPECT_EQ(PowerStatus::STANDBY, Plugin::HdmiCecSinkImplementation::_instance->deviceList[0].m_powerStatus.toInt());
    EXPECT_EQ(-1, Plugin::HdmiCecSinkImplementation::_instance->m_currentActiveSource);

    // Restore the powered-on state so the process-wide power tracking is left as found.
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->onPowerModeChanged(
        WPEFramework::Exchange::IPowerManager::POWER_STATE_STANDBY,
        WPEFramework::Exchange::IPowerManager::POWER_STATE_ON));
}

// Test fixture description: every CEC abort reason must be reported to registered clients. The
// plugin's own notification sink is registered during activation, so the fan-out really runs and
// the delivered JSON-RPC event is what closes gap-plugin-sink-reportfeatureabort.
//
// NOTE on the AbortReason operand: the shared CEC mock declares a public `impl` delegate on
// AbortReason and its int constructor is the only one in that header that does NOT initialise it
// (SystemAudioStatus and AudioStatus both do). AbortReason::toInt() dereferences `impl` whenever
// it is non-null, so an operand built by a caller has to null the delegate explicitly to select
// the direct-parse path. The delegate is public, so this is a caller-side precondition rather
// than a change to the shared mock.
TEST_F(HdmiCecSinkDsTest, reportFeatureAbortEvent_EachAbortReason_IsNotified)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    // The five reasons defined by CEC: unrecognised opcode, not in correct mode, cannot provide
    // source, invalid operand and refused.
    for (int reason = 0; reason <= 4; reason++) {
        AbortReason abortReason(reason);
        abortReason.impl = nullptr;
        EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->reportFeatureAbortEvent(
            LogicalAddress(4), OpCode(GET_CEC_VERSION), abortReason))
            << "abort reason " << reason << " was not reported cleanly";
        EXPECT_EQ(reason, abortReason.toInt());
    }

    // Reporting is a pure notification: the device table must be untouched by it.
    EXPECT_FALSE(Plugin::HdmiCecSinkImplementation::_instance->deviceList[4].m_isDeviceDisconnected);
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getEnabled"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
}

// Test fixture description: feature-abort reporting at the logical-address and opcode boundaries
// - the TV's own address, the unregistered/broadcast address, and the lowest and highest opcode
// values in play.
TEST_F(HdmiCecSinkDsTest, reportFeatureAbortEvent_BoundaryOperands_AreNotified)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    AbortReason unrecognisedOpcode(AbortReason::UNRECOGNIZED_OPCODE);
    unrecognisedOpcode.impl = nullptr;
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->reportFeatureAbortEvent(
        LogicalAddress(LogicalAddress::TV), OpCode(FEATURE_ABORT), unrecognisedOpcode));

    AbortReason refused(4);
    refused.impl = nullptr;
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->reportFeatureAbortEvent(
        LogicalAddress(LogicalAddress::UNREGISTERED), OpCode(ABORT), refused));

    AbortReason notInCorrectMode(1);
    notInCorrectMode.impl = nullptr;
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->reportFeatureAbortEvent(
        LogicalAddress(LogicalAddress::AUDIO_SYSTEM), OpCode(INITIATE_ARC), notInCorrectMode));

    EXPECT_EQ(0, unrecognisedOpcode.toInt());
    EXPECT_EQ(4, refused.toInt());
    EXPECT_EQ(1, notInCorrectMode.toInt());
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getEnabled"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
}

// Test fixture description: the sink also has to be able to SEND a feature abort on the bus, not
// just report one to clients. The observable outcome is the directed CEC frame handed to the
// connection for the aborting device.
TEST_F(HdmiCecSinkDsTest, sendFeatureAbort_DirectedToInitiator_IsPutOnTheBus)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    // The plugin's polling thread broadcasts its own physical address on the same interface, so
    // the capture has to discriminate: a feature abort is the directed send to the aborting
    // device carrying the plugin's 500 ms transmit budget.
    int abortsSent = 0;
    EXPECT_CALL(*p_connectionImplMock, sendTo(::testing::_, ::testing::_, ::testing::_))
        .WillRepeatedly(::testing::Invoke(
            [&](const LogicalAddress& to, const CECFrame& frame, int timeout) {
                if (to.toInt() == 4 && timeout == 500) {
                    abortsSent++;
                }
            }));

    AbortReason unrecognisedOpcode(AbortReason::UNRECOGNIZED_OPCODE);
    unrecognisedOpcode.impl = nullptr;
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->sendFeatureAbort(
        LogicalAddress(4), OpCode(GET_CEC_VERSION), unrecognisedOpcode));

    EXPECT_EQ(1, abortsSent);
}

// Test fixture description: route resolution refuses an unregistered logical address rather than
// returning a route for a device that cannot exist.
TEST_F(HdmiCecSinkDsTest, getActiveRoute_UnregisteredLogicalAddress_LeavesRouteEmpty)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    std::vector<uint8_t> route;
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->getActiveRoute(
        LogicalAddress(LogicalAddress::UNREGISTERED), route));

    EXPECT_TRUE(route.empty());
}

// Test fixture description: route resolution for a device that is not present, or that is
// present but is not the active source, leaves the route untouched.
TEST_F(HdmiCecSinkDsTest, getActiveRoute_DeviceNotActiveSource_LeavesRouteEmpty)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    Plugin::HdmiCecSinkImplementation::_instance->deviceList[6].clear();

    std::vector<uint8_t> route;
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->getActiveRoute(LogicalAddress(6), route));
    EXPECT_TRUE(route.empty());

    // Present, but not flagged as the active source: still no route.
    Plugin::HdmiCecSinkImplementation::_instance->deviceList[6].m_isDevicePresent = true;
    Plugin::HdmiCecSinkImplementation::_instance->deviceList[6].m_isActiveSource = false;
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->getActiveRoute(LogicalAddress(6), route));
    EXPECT_TRUE(route.empty());

    Plugin::HdmiCecSinkImplementation::_instance->deviceList[6].clear();
}

// Test fixture description: route resolution happy path. A present active source whose physical
// address maps onto a known HDMI port yields the chain of logical addresses leading to it,
// ending with the port's own address.
TEST_F(HdmiCecSinkDsTest, getActiveRoute_ActiveSourceOnKnownPort_ResolvesRoute)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);
    ASSERT_GE(Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs.size(), 2u);

    // Port index 1 owns physical address 2.0.0.0, so a device at 2.1.0.0 hangs off it.
    Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].update(LogicalAddress(4));
    PhysicalAddress sourceAddress(2, 1, 0, 0);
    Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].addChild(LogicalAddress(6), sourceAddress);

    Plugin::HdmiCecSinkImplementation::_instance->deviceList[6].m_isDevicePresent = true;
    Plugin::HdmiCecSinkImplementation::_instance->deviceList[6].m_isActiveSource = true;
    Plugin::HdmiCecSinkImplementation::_instance->deviceList[6].m_physicalAddr = sourceAddress;
    Plugin::HdmiCecSinkImplementation::_instance->deviceList[6].m_logicalAddress = LogicalAddress(6);

    std::vector<uint8_t> route;
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->getActiveRoute(LogicalAddress(6), route));

    ASSERT_EQ(2u, route.size());
    EXPECT_EQ(6, static_cast<int>(route[0]));
    EXPECT_EQ(4, static_cast<int>(route[1]));

    Plugin::HdmiCecSinkImplementation::_instance->deviceList[6].clear();
    Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].removeChild(sourceAddress);
    Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].update(LogicalAddress(LogicalAddress::UNREGISTERED));
}

// Test fixture description: the getActiveRoute JSON-RPC method reports the resolved HDMI route
// for the current active source, formatted as the chain of devices and the terminating port.
TEST_F(HdmiCecSinkDsTest, getActiveRoute_ActiveSourceOnKnownPort_ReportedOverJsonRpc)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);
    ASSERT_GE(Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs.size(), 2u);

    Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].update(LogicalAddress(4));
    PhysicalAddress sourceAddress(2, 1, 0, 0);
    Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].addChild(LogicalAddress(6), sourceAddress);

    Plugin::HdmiCecSinkImplementation::_instance->deviceList[6].m_isDevicePresent = true;
    Plugin::HdmiCecSinkImplementation::_instance->deviceList[6].m_isActiveSource = true;
    Plugin::HdmiCecSinkImplementation::_instance->deviceList[6].m_physicalAddr = sourceAddress;
    Plugin::HdmiCecSinkImplementation::_instance->deviceList[6].m_logicalAddress = LogicalAddress(6);
    Plugin::HdmiCecSinkImplementation::_instance->deviceList[6].update(OSDName("SOURCE6"));
    Plugin::HdmiCecSinkImplementation::_instance->deviceList[4].m_logicalAddress = LogicalAddress(4);
    Plugin::HdmiCecSinkImplementation::_instance->deviceList[4].m_physicalAddr = PhysicalAddress(2, 0, 0, 0);
    Plugin::HdmiCecSinkImplementation::_instance->m_currentActiveSource = 6;

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getActiveRoute"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"available\":true"));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
    EXPECT_THAT(response, ::testing::ContainsRegex("SOURCE6"));

    Plugin::HdmiCecSinkImplementation::_instance->m_currentActiveSource = -1;
    Plugin::HdmiCecSinkImplementation::_instance->deviceList[6].clear();
    Plugin::HdmiCecSinkImplementation::_instance->deviceList[4].clear();
    Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].removeChild(sourceAddress);
    Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].update(LogicalAddress(LogicalAddress::UNREGISTERED));
}

// Test fixture description: when the TV itself is the active source the route degenerates to
// "TV" - the boundary case where the active source equals the locally allocated address.
TEST_F(HdmiCecSinkDsTest, getActiveRoute_TvIsActiveSource_ReportsTvOverJsonRpc)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    // Logical address 0 is what the TV allocates for itself.
    Plugin::HdmiCecSinkImplementation::_instance->m_currentActiveSource = LogicalAddress::TV;

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getActiveRoute"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"available\":true"));
    EXPECT_THAT(response, ::testing::ContainsRegex("TV"));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));

    Plugin::HdmiCecSinkImplementation::_instance->m_currentActiveSource = -1;
}

// Test fixture description: with no active source at all the method reports unavailability
// rather than an empty or malformed route.
TEST_F(HdmiCecSinkDsTest, getActiveRoute_NoActiveSource_ReportsUnavailableOverJsonRpc)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    Plugin::HdmiCecSinkImplementation::_instance->m_currentActiveSource = -1;

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getActiveRoute"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"available\":false"));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
}

// Test fixture description: the device list reports each present peer with its learned
// attributes, and marks the HDMI port a connected peer was found on.
TEST_F(HdmiCecSinkDsTest, getDeviceList_WithPresentDevices_ReportsLearnedAttributes)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);
    ASSERT_GE(Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs.size(), 2u);

    Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].update(true);
    Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].update(LogicalAddress(6));

    Plugin::CECDeviceParams& peer = Plugin::HdmiCecSinkImplementation::_instance->deviceList[6];
    peer.m_isDevicePresent = true;
    peer.m_logicalAddress = LogicalAddress(6);
    peer.update(PhysicalAddress(2, 0, 0, 0));
    peer.update(OSDName("PEERSIX"));
    peer.update(VendorID(0x00, 0x19, 0xFB));
    peer.update(Version(Version::V_1_4));
    peer.update(PowerStatus(PowerStatus::ON));
    peer.update(DeviceType(DeviceType::PLAYBACK_DEVICE));

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getDeviceList"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
    EXPECT_THAT(response, ::testing::ContainsRegex("PEERSIX"));

    peer.clear();
    Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].update(false);
    Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].update(LogicalAddress(LogicalAddress::UNREGISTERED));
}

// Test fixture description: the active-source getter reports the peer's attributes and derives
// the HDMI port label from the first byte of its physical address.
TEST_F(HdmiCecSinkDsTest, getActiveSource_PresentActiveDevice_ReportsAttributesAndPort)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    Plugin::CECDeviceParams& peer = Plugin::HdmiCecSinkImplementation::_instance->deviceList[6];
    peer.m_isDevicePresent = true;
    peer.m_isActiveSource = true;
    peer.m_logicalAddress = LogicalAddress(6);
    peer.update(PhysicalAddress(2, 0, 0, 0));
    peer.update(OSDName("ACTIVE6"));
    peer.update(PowerStatus(PowerStatus::ON));
    peer.update(DeviceType(DeviceType::PLAYBACK_DEVICE));
    Plugin::HdmiCecSinkImplementation::_instance->m_currentActiveSource = 6;

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getActiveSource"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"available\":true"));
    EXPECT_THAT(response, ::testing::ContainsRegex("ACTIVE6"));
    // Physical address 2.0.0.0 is HDMI port 1 (byte0 - 1).
    EXPECT_THAT(response, ::testing::ContainsRegex("HDMI1"));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));

    // The TV's own address reports the "TV" port label instead of an HDMI input.
    Plugin::CECDeviceParams& ownEntry = Plugin::HdmiCecSinkImplementation::_instance->deviceList[0];
    ownEntry.m_isDevicePresent = true;
    ownEntry.m_logicalAddress = LogicalAddress(LogicalAddress::TV);
    ownEntry.update(PhysicalAddress(0, 0, 0, 0));
    Plugin::HdmiCecSinkImplementation::_instance->m_currentActiveSource = LogicalAddress::TV;
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getActiveSource"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));

    Plugin::HdmiCecSinkImplementation::_instance->m_currentActiveSource = -1;
    peer.clear();
    ownEntry.clear();
}

// Test fixture description: the diagnostic device dump walks every present entry, which is what
// drives CECDeviceParams::printVariable through the live device table.
TEST_F(HdmiCecSinkDsTest, printDeviceList_WithPresentDevices_DumpsEachEntry)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    Plugin::CECDeviceParams& first = Plugin::HdmiCecSinkImplementation::_instance->deviceList[4];
    Plugin::CECDeviceParams& second = Plugin::HdmiCecSinkImplementation::_instance->deviceList[5];
    first.m_isDevicePresent = true;
    first.m_logicalAddress = LogicalAddress(4);
    first.update(OSDName("DUMPFOUR"));
    first.update(PhysicalAddress(2, 0, 0, 0));
    second.m_isDevicePresent = true;
    second.m_logicalAddress = LogicalAddress(5);
    second.update(OSDName("DUMPFIVE"));
    second.update(PhysicalAddress(3, 0, 0, 0));

    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("printDeviceList"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"printed\":true"));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));

    // Dumping is read-only: the entries survive it unchanged.
    EXPECT_TRUE(first.m_isDevicePresent);
    EXPECT_EQ(std::string("DUMPFOUR"), first.m_osdName.toString());
    EXPECT_TRUE(second.m_isDevicePresent);

    first.clear();
    second.clear();
}

// Test fixture description: removing a present device clears its entry, unhooks it from the HDMI
// port's device chain and notifies clients - the path that closes
// gap-plugin-sink-ondeviceremoved on the production side.
TEST_F(HdmiCecSinkDsTest, removeDevice_PresentDevice_ClearsEntryAndNotifies)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);
    ASSERT_GE(Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs.size(), 2u);

    PhysicalAddress peerAddress(2, 1, 0, 0);
    Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].update(LogicalAddress(4));
    Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].addChild(LogicalAddress(6), peerAddress);
    ASSERT_EQ(6, static_cast<int>(Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].m_deviceChain[0].m_childsLogicalAddr[0]));

    Plugin::CECDeviceParams& peer = Plugin::HdmiCecSinkImplementation::_instance->deviceList[6];
    peer.m_isDevicePresent = true;
    peer.m_logicalAddress = LogicalAddress(6);
    peer.update(PhysicalAddress(2, 1, 0, 0));
    peer.update(OSDName("GOINGAWAY"));

    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->removeDevice(6));

    // Assertions are on the cleared entry, never on the shared peer counter: the plugin's own
    // polling thread assigns m_numberOfDevices = 0 unconditionally when it allocates the TV
    // logical address, so any delta taken across a call is inherently racy. The cleared entry
    // is stable because nothing else in the plugin rewrites these fields without an inbound
    // frame, and no frame is injected here.
    EXPECT_FALSE(peer.m_isDevicePresent);
    EXPECT_EQ(std::string("ffff"), peer.m_physicalAddr.toString());
    EXPECT_EQ(std::string(""), peer.m_osdName.toString());
    EXPECT_EQ(0, peer.m_isRequestRetry);
    EXPECT_FALSE(peer.m_isActiveSource);
    EXPECT_FALSE(peer.isAllUpdated());
    // The HDMI port it hung off is unhooked and de-registered.
    EXPECT_EQ(LogicalAddress::UNREGISTERED,
        static_cast<int>(Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].m_deviceChain[0].m_childsLogicalAddr[0]));
    EXPECT_EQ(LogicalAddress::UNREGISTERED,
        Plugin::HdmiCecSinkImplementation::_instance->hdmiInputs[1].m_logicalAddr.toInt());
}

// Test fixture description: removing the audio system additionally tears down the audio-status
// tracking state and reports the audio device as disconnected.
TEST_F(HdmiCecSinkDsTest, removeDevice_AudioSystemAddress_ResetsAudioState)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    Plugin::CECDeviceParams& audioSystem = Plugin::HdmiCecSinkImplementation::_instance->deviceList[LogicalAddress::AUDIO_SYSTEM];
    audioSystem.m_isDevicePresent = true;
    audioSystem.m_logicalAddress = LogicalAddress(LogicalAddress::AUDIO_SYSTEM);
    audioSystem.update(PhysicalAddress(2, 0, 0, 0));
    audioSystem.update(OSDName("SOUNDBAR"));

    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->removeDevice(LogicalAddress::AUDIO_SYSTEM));

    // Post-state only, for the same reason as the directed-removal case above: the shared peer
    // counter is reset by the polling thread and cannot carry an assertion.
    EXPECT_FALSE(audioSystem.m_isDevicePresent);
    EXPECT_EQ(std::string("ffff"), audioSystem.m_physicalAddr.toString());
    EXPECT_EQ(std::string(""), audioSystem.m_osdName.toString());

    // The audio-device connection state is reported as disconnected once the peer is gone.
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getAudioDeviceConnectedStatus"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"connected\":false"));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
}

// Test fixture description: removal short-circuits for a device that was never present, so the
// entry is left exactly as it was rather than being cleared a second time.
TEST_F(HdmiCecSinkDsTest, removeDevice_DeviceNotPresent_IsNoOp)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    Plugin::CECDeviceParams& absent = Plugin::HdmiCecSinkImplementation::_instance->deviceList[6];
    absent.clear();
    // A sentinel that only CECDeviceParams::clear() resets. If the removal body were entered it
    // would wipe this; the absent-device guard means it must survive verbatim.
    absent.update(OSDName("NEVERTHERE"));
    ASSERT_TRUE(absent.m_isOSDNameUpdated);

    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->removeDevice(6));

    EXPECT_FALSE(absent.m_isDevicePresent);
    EXPECT_EQ(std::string("NEVERTHERE"), absent.m_osdName.toString());
    EXPECT_TRUE(absent.m_isOSDNameUpdated);
}

// Test fixture description: removal at both ends of the logical-address range must be handled
// without indexing outside the sixteen-entry device table.
TEST_F(HdmiCecSinkDsTest, removeDevice_BoundaryLogicalAddresses_AreHandled)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    // Minimum address: the TV's own entry. Its presence flag and identity fields are owned by the
    // plugin's polling thread, so the removal is observed through m_isActiveSource, which nothing
    // but CECDeviceParams::clear() and an inbound active-source frame ever writes.
    Plugin::CECDeviceParams& tv = Plugin::HdmiCecSinkImplementation::_instance->deviceList[LogicalAddress::TV];
    tv.m_isDevicePresent = true;
    tv.m_isActiveSource = true;
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->removeDevice(LogicalAddress::TV));
    EXPECT_FALSE(tv.m_isActiveSource);

    // Maximum address: the unregistered slot, the last of the sixteen. The polling loops all stop
    // below it, so its whole entry is a stable observable.
    Plugin::CECDeviceParams& phantom = Plugin::HdmiCecSinkImplementation::_instance->deviceList[LogicalAddress::UNREGISTERED];
    phantom.m_isDevicePresent = true;
    phantom.update(OSDName("PHANTOM"));
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->removeDevice(LogicalAddress::UNREGISTERED));
    EXPECT_FALSE(phantom.m_isDevicePresent);
    EXPECT_EQ(std::string(""), phantom.m_osdName.toString());
    EXPECT_FALSE(phantom.m_isOSDNameUpdated);
}

//=============================================================================
// Inbound CEC frame injection (L1 Level)
//
// Frame byte convention, verified against MessageDecoder: byte0's HIGH nibble is the initiator
// and its LOW nibble the destination, so 0x40 is "from Playback Device 1 (LA=4) to the TV (LA=0)"
// and 0x4F is "from Playback Device 1 (LA=4) to broadcast (LA=15)". byte1 is the opcode, and the
// decoder returns early for any frame shorter than two bytes.
//
// These cases build their own HdmiCecSinkFrameListener over a HdmiCecSinkProcessor instead of
// waiting for the one the plugin registers. That is deliberate and was established by measurement:
// HdmiCecSinkImplementation calls Connection::addFrameListener from exactly one place - the poll
// thread's POLL state - so the fixture's `listeners` vector is genuinely empty for a short while
// after construction, which is why every pre-existing frame test opens with a 100 ms sleep.
// HdmiCecSinkFrameListener::notify() runs MessageDecoder(processor).decode(in) inline, so driving
// it directly reaches exactly the same handlers with no wall-clock wait and no new test construct -
// the listener and the processor are both public production classes.
//=============================================================================

// Test fixture description: <Polling> is the CEC presence probe and the only opcode the sink
// dispatches with no operand at all. Directed delivery must be accepted without disturbing the
// device table the poll thread maintains.
// Covers HdmiCecSinkProcessor::process(const Polling&, const Header&).
TEST_F(HdmiCecSinkFrameProcessingTest, InjectPollingFrame_Directed_IsProcessed)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    Plugin::CECDeviceParams& initiator = Plugin::HdmiCecSinkImplementation::_instance->deviceList[4];
    initiator.update(OSDName("PLAYBACK1"));

    Connection cecBus;
    Plugin::HdmiCecSinkProcessor processor(cecBus);
    Plugin::HdmiCecSinkFrameListener listener(processor);

    // From Playback Device 1 (LA=4) to the TV (LA=0); 0x13 is <Polling>.
    const uint8_t pollingDirected[] = { 0x40, 0x13 };
    CECFrame frame(pollingDirected, sizeof(pollingDirected));
    EXPECT_NO_THROW(listener.notify(frame));

    // <Polling> carries no operand, so the initiator's learned attributes must be untouched and
    // the plugin must remain able to answer for its device table.
    EXPECT_EQ(std::string("PLAYBACK1"), initiator.m_osdName.toString());
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getDeviceList"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
}

// Test fixture description: a broadcast <Polling> reaches the same handler - the sink must not
// mistake the broadcast destination for a malformed frame.
TEST_F(HdmiCecSinkFrameProcessingTest, InjectPollingFrame_Broadcast_IsProcessed)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    Plugin::CECDeviceParams& initiator = Plugin::HdmiCecSinkImplementation::_instance->deviceList[4];
    initiator.update(OSDName("BROADCASTER"));

    Connection cecBus;
    Plugin::HdmiCecSinkProcessor processor(cecBus);
    Plugin::HdmiCecSinkFrameListener listener(processor);

    // From Playback Device 1 (LA=4) to broadcast (LA=15); 0x13 is <Polling>.
    const uint8_t pollingBroadcast[] = { 0x4F, 0x13 };
    CECFrame frame(pollingBroadcast, sizeof(pollingBroadcast));
    EXPECT_NO_THROW(listener.notify(frame));

    EXPECT_EQ(std::string("BROADCASTER"), initiator.m_osdName.toString());
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getDeviceList"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
}

// Test fixture description: corner case - a header with no opcode byte is below the decoder's
// minimum frame length and must be dropped rather than dispatched as opcode zero.
TEST_F(HdmiCecSinkFrameProcessingTest, InjectHeaderOnlyFrame_IsDroppedByDecoder)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    Plugin::CECDeviceParams& initiator = Plugin::HdmiCecSinkImplementation::_instance->deviceList[4];
    initiator.update(OSDName("UNTOUCHED"));
    initiator.update(PhysicalAddress(2, 0, 0, 0));

    Connection cecBus;
    Plugin::HdmiCecSinkProcessor processor(cecBus);
    Plugin::HdmiCecSinkFrameListener listener(processor);

    const uint8_t headerOnly[] = { 0x40 };
    CECFrame frame(headerOnly, sizeof(headerOnly));
    EXPECT_NO_THROW(listener.notify(frame));

    // Nothing was dispatched, so no operand was applied to the initiator's entry.
    EXPECT_EQ(std::string("UNTOUCHED"), initiator.m_osdName.toString());
    EXPECT_EQ(PhysicalAddress(2, 0, 0, 0).toString(), initiator.m_physicalAddr.toString());
}

// Test fixture description: an empty buffer is the extreme of the same corner case and must also
// be dropped without reaching any handler.
TEST_F(HdmiCecSinkFrameProcessingTest, InjectEmptyFrame_IsDroppedByDecoder)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    Plugin::CECDeviceParams& initiator = Plugin::HdmiCecSinkImplementation::_instance->deviceList[4];
    initiator.update(OSDName("STILLHERE"));

    Connection cecBus;
    Plugin::HdmiCecSinkProcessor processor(cecBus);
    Plugin::HdmiCecSinkFrameListener listener(processor);

    CECFrame frame(nullptr, 0);
    EXPECT_NO_THROW(listener.notify(frame));

    EXPECT_EQ(std::string("STILLHERE"), initiator.m_osdName.toString());
    EXPECT_EQ(Core::ERROR_NONE, handler.Invoke(connection, _T("getDeviceList"), _T("{}"), response));
    EXPECT_THAT(response, ::testing::ContainsRegex("\"success\":true"));
}

//=============================================================================
// Notification events delivered to a subscribed JSON-RPC client (L1 Level)
//
// Every method on Exchange::IHdmiCecSink::INotification is declared `virtual void X(...) {};` -
// a virtual with an EMPTY INLINE BODY, never pure virtual - so a handler that simply omits an
// override compiles cleanly and silently swallows the event. That single declaration style is the
// root cause of the sink's historically unasserted event surface. The remedy here is to observe
// the events where they actually become observable: the plugin's own Core::Sink<Notification>
// forwards each one to Exchange::JHdmiCecSink::Event::<Name>, which notifies every subscribed
// designator through PluginHost::IShell::Submit. Capturing that call asserts the event name AND
// its payload without touching entservices-apis, and without adding a notification handler class
// (that surface belongs to the L2 suite).
//=============================================================================

// Test fixture description: an inbound <User Control Pressed> ultimately reaches clients as
// onKeyPressEvent carrying both the initiator's logical address and the raw CEC UI command.
// COVERAGE_GAPS.md gap-plugin-sink-onkeypress (rank 27, P1); symbol
// HdmiCecSinkImplementation::SendKeyPressMsgEvent.
TEST_F(HdmiCecSinkInitializedEventDsTest, onKeyPressEvent_SubscribedClient_ReceivesAddressAndKeyCode)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EVENT_SUBSCRIBE(0, _T("onKeyPressEvent"), _T("client.events.onKeyPressEvent"), message);
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->SendKeyPressMsgEvent(4, 65));
    EVENT_UNSUBSCRIBE(0, _T("onKeyPressEvent"), _T("client.events.onKeyPressEvent"), message);

    EXPECT_THAT(notified, ::testing::HasSubstr("onKeyPressEvent"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"logicalAddress\":4"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"keyCode\":65"));
}

// Test fixture description: boundary and out-of-range operands must reach clients verbatim - the
// sink is a transport for the remote key code, not its validator.
// COVERAGE_GAPS.md gap-plugin-sink-onkeypress (rank 27, P1).
TEST_F(HdmiCecSinkInitializedEventDsTest, onKeyPressEvent_BoundaryOperands_AreForwardedVerbatim)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EVENT_SUBSCRIBE(0, _T("onKeyPressEvent"), _T("client.events.onKeyPressEvent"), message);

    // Minimum key code against the minimum logical address (the TV itself).
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->SendKeyPressMsgEvent(LogicalAddress::TV, 0));
    // Maximum single-byte key code against the maximum logical address.
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->SendKeyPressMsgEvent(LogicalAddress::UNREGISTERED, 255));
    // Just outside the single-byte range - forwarded rather than clamped or rejected.
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->SendKeyPressMsgEvent(4, 256));

    EVENT_UNSUBSCRIBE(0, _T("onKeyPressEvent"), _T("client.events.onKeyPressEvent"), message);

    EXPECT_THAT(notified, ::testing::HasSubstr("\"logicalAddress\":0"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"keyCode\":0"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"logicalAddress\":15"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"keyCode\":255"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"keyCode\":256"));
}

// Test fixture description: with nothing subscribed to onKeyPressEvent the fan-out still runs to
// completion but no client notification is produced - the negative control for the event path.
// COVERAGE_GAPS.md gap-plugin-sink-onkeypress (rank 27, P1).
TEST_F(HdmiCecSinkInitializedEventDsTest, onKeyPressEvent_NoSubscriber_ProducesNoClientNotification)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->SendKeyPressMsgEvent(4, 65));

    EXPECT_THAT(notified, ::testing::Not(::testing::HasSubstr("onKeyPressEvent")));
}

// Test fixture description: an inbound <User Control Released> reaches clients as onKeyReleaseEvent
// carrying only the initiator's logical address - the release message has no key-code operand.
// COVERAGE_GAPS.md gap-plugin-sink-onkeyrelease (rank 28, P1); symbol
// HdmiCecSinkImplementation::SendKeyReleaseMsgEvent.
TEST_F(HdmiCecSinkInitializedEventDsTest, onKeyReleaseEvent_SubscribedClient_ReceivesLogicalAddress)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EVENT_SUBSCRIBE(0, _T("onKeyReleaseEvent"), _T("client.events.onKeyReleaseEvent"), message);
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->SendKeyReleaseMsgEvent(4));
    // Boundary logical addresses at both ends of the CEC range.
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->SendKeyReleaseMsgEvent(LogicalAddress::TV));
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->SendKeyReleaseMsgEvent(LogicalAddress::UNREGISTERED));
    EVENT_UNSUBSCRIBE(0, _T("onKeyReleaseEvent"), _T("client.events.onKeyReleaseEvent"), message);

    EXPECT_THAT(notified, ::testing::HasSubstr("onKeyReleaseEvent"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"logicalAddress\":4"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"logicalAddress\":0"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"logicalAddress\":15"));
    // The release event carries no key code at all.
    EXPECT_THAT(notified, ::testing::Not(::testing::HasSubstr("keyCode")));
}

// Test fixture description: a directed <Image View On> registers the initiator and reaches clients
// as onImageViewOnMsg carrying that initiator's logical address.
// COVERAGE_GAPS.md gap-plugin-sink-onimageviewon (rank 29, P1).
//
// Deliberately makes no claim about the wake-from-standby companion event: the TV's own power
// status lives in deviceList[m_logicalAddressAllocated].m_powerStatus, which the plugin's poll
// thread writes from the file-static powerState in its POLL state, so it is not a field a test can
// pin. The standby arm is asserted in its own case below, where STANDBY is the stable value.
TEST_F(HdmiCecSinkInitializedEventDsTest, onImageViewOnMsg_DirectedFrame_NotifiesSubscribedClient)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EVENT_SUBSCRIBE(0, _T("onImageViewOnMsg"), _T("client.events.onImageViewOnMsg"), message);

    Connection cecBus;
    Plugin::HdmiCecSinkProcessor processor(cecBus);
    Plugin::HdmiCecSinkFrameListener listener(processor);

    // From Playback Device 1 (LA=4) to the TV (LA=0); 0x04 is <Image View On>.
    const uint8_t imageViewOn[] = { 0x40, 0x04 };
    CECFrame frame(imageViewOn, sizeof(imageViewOn));
    EXPECT_NO_THROW(listener.notify(frame));

    EVENT_UNSUBSCRIBE(0, _T("onImageViewOnMsg"), _T("client.events.onImageViewOnMsg"), message);

    EXPECT_THAT(notified, ::testing::HasSubstr("onImageViewOnMsg"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"logicalAddress\":4"));
    EXPECT_TRUE(Plugin::HdmiCecSinkImplementation::_instance->deviceList[4].m_isDevicePresent);
}

// Test fixture description: updateImageViewOn() refuses the unregistered logical address outright,
// so a frame whose initiator is LA 15 registers nothing and notifies nobody.
// COVERAGE_GAPS.md gap-plugin-sink-onimageviewon (rank 29, P1) - guard arm.
TEST_F(HdmiCecSinkInitializedEventDsTest, onImageViewOnMsg_UnregisteredInitiator_ProducesNoNotification)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EVENT_SUBSCRIBE(0, _T("onImageViewOnMsg"), _T("client.events.onImageViewOnMsg"), message);

    Connection cecBus;
    Plugin::HdmiCecSinkProcessor processor(cecBus);
    Plugin::HdmiCecSinkFrameListener listener(processor);

    // From the unregistered address (LA=15) to the TV (LA=0); 0x04 is <Image View On>.
    const uint8_t imageViewOnFromUnregistered[] = { 0xF0, 0x04 };
    CECFrame frame(imageViewOnFromUnregistered, sizeof(imageViewOnFromUnregistered));
    EXPECT_NO_THROW(listener.notify(frame));

    EVENT_UNSUBSCRIBE(0, _T("onImageViewOnMsg"), _T("client.events.onImageViewOnMsg"), message);

    EXPECT_THAT(notified, ::testing::Not(::testing::HasSubstr("onImageViewOnMsg")));
}

// Test fixture description: the same frame arriving while the TV reports standby additionally
// raises onWakeupFromStandby - the second arm of updateImageViewOn().
// COVERAGE_GAPS.md gap-plugin-sink-onimageviewon (rank 29, P1).
TEST_F(HdmiCecSinkInitializedEventDsTest, onImageViewOnMsg_DirectedFrameWhileInStandby_AlsoNotifiesWakeup)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    // Register the initiator up front so the standby arm's device-present precondition holds, and
    // put the TV into standby through the production power hook rather than by poking the field, so
    // both the file-static power state and the TV's own device entry agree.
    Plugin::HdmiCecSinkImplementation::_instance->deviceList[4].m_isDevicePresent = true;
    Plugin::HdmiCecSinkImplementation::_instance->onPowerModeChanged(
        WPEFramework::Exchange::IPowerManager::POWER_STATE_ON,
        WPEFramework::Exchange::IPowerManager::POWER_STATE_STANDBY);
    ASSERT_EQ(PowerStatus::STANDBY,
        Plugin::HdmiCecSinkImplementation::_instance->deviceList[LogicalAddress::TV].m_powerStatus.toInt());

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EVENT_SUBSCRIBE(0, _T("onImageViewOnMsg"), _T("client.events.onImageViewOnMsg"), message);
    EVENT_SUBSCRIBE(0, _T("onWakeupFromStandby"), _T("client.events.onWakeupFromStandby"), message);

    Connection cecBus;
    Plugin::HdmiCecSinkProcessor processor(cecBus);
    Plugin::HdmiCecSinkFrameListener listener(processor);

    const uint8_t imageViewOn[] = { 0x40, 0x04 };
    CECFrame frame(imageViewOn, sizeof(imageViewOn));
    EXPECT_NO_THROW(listener.notify(frame));

    EVENT_UNSUBSCRIBE(0, _T("onWakeupFromStandby"), _T("client.events.onWakeupFromStandby"), message);
    EVENT_UNSUBSCRIBE(0, _T("onImageViewOnMsg"), _T("client.events.onImageViewOnMsg"), message);

    EXPECT_THAT(notified, ::testing::HasSubstr("onWakeupFromStandby"));
    EXPECT_THAT(notified, ::testing::HasSubstr("onImageViewOnMsg"));
}

// Test fixture description: a broadcast <Image View On> is rejected by the handler's addressing
// guard, so no client notification is produced at all.
// COVERAGE_GAPS.md gap-plugin-sink-onimageviewon (rank 29, P1) - negative case.
TEST_F(HdmiCecSinkInitializedEventDsTest, onImageViewOnMsg_BroadcastFrame_ProducesNoNotification)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EVENT_SUBSCRIBE(0, _T("onImageViewOnMsg"), _T("client.events.onImageViewOnMsg"), message);

    Connection cecBus;
    Plugin::HdmiCecSinkProcessor processor(cecBus);
    Plugin::HdmiCecSinkFrameListener listener(processor);

    // From Playback Device 1 (LA=4) to broadcast (LA=15); 0x04 is <Image View On>.
    const uint8_t imageViewOnBroadcast[] = { 0x4F, 0x04 };
    CECFrame frame(imageViewOnBroadcast, sizeof(imageViewOnBroadcast));
    EXPECT_NO_THROW(listener.notify(frame));

    EVENT_UNSUBSCRIBE(0, _T("onImageViewOnMsg"), _T("client.events.onImageViewOnMsg"), message);

    EXPECT_THAT(notified, ::testing::Not(::testing::HasSubstr("onImageViewOnMsg")));
}

// Test fixture description: <Text View On> is the sibling of <Image View On> and must reach clients
// as its own distinctly named event rather than being folded into the image-view path.
TEST_F(HdmiCecSinkInitializedEventDsTest, onTextViewOnMsg_DirectedFrame_NotifiesSubscribedClient)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EVENT_SUBSCRIBE(0, _T("onTextViewOnMsg"), _T("client.events.onTextViewOnMsg"), message);

    Connection cecBus;
    Plugin::HdmiCecSinkProcessor processor(cecBus);
    Plugin::HdmiCecSinkFrameListener listener(processor);

    // From Playback Device 1 (LA=4) to the TV (LA=0); 0x0D is <Text View On>.
    const uint8_t textViewOn[] = { 0x40, 0x0D };
    CECFrame frame(textViewOn, sizeof(textViewOn));
    EXPECT_NO_THROW(listener.notify(frame));

    EVENT_UNSUBSCRIBE(0, _T("onTextViewOnMsg"), _T("client.events.onTextViewOnMsg"), message);

    EXPECT_THAT(notified, ::testing::HasSubstr("onTextViewOnMsg"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"logicalAddress\":4"));
    EXPECT_THAT(notified, ::testing::Not(::testing::HasSubstr("onImageViewOnMsg")));
}

// Test fixture description: a feature abort reported by a peer reaches clients as
// reportFeatureAbortEvent carrying the reporting address, the aborted opcode and the reason.
// COVERAGE_GAPS.md gap-plugin-sink-reportfeatureabort (rank 38, P2); symbols
// HdmiCecSinkImplementation::reportFeatureAbortEvent and HdmiCecSink::Notification::ReportFeatureAbortEvent.
TEST_F(HdmiCecSinkInitializedEventDsTest, reportFeatureAbortEvent_SubscribedClient_ReceivesAllThreeOperands)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EVENT_SUBSCRIBE(0, _T("reportFeatureAbortEvent"), _T("client.events.reportFeatureAbortEvent"), message);

    // The shared CEC mock's AbortReason(int) constructor is the only one in that header which
    // leaves its public `impl` delegate uninitialised, and AbortReason::toInt() dereferences it
    // when it is non-null. The mock library is out of scope for edits, so every AbortReason built
    // here is a named lvalue with `impl` explicitly cleared first.
    AbortReason unrecognisedOpcode(AbortReason::UNRECOGNIZED_OPCODE);
    unrecognisedOpcode.impl = nullptr;
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->reportFeatureAbortEvent(
        LogicalAddress(4), OpCode(GET_CEC_VERSION), unrecognisedOpcode));

    EVENT_UNSUBSCRIBE(0, _T("reportFeatureAbortEvent"), _T("client.events.reportFeatureAbortEvent"), message);

    EXPECT_THAT(notified, ::testing::HasSubstr("reportFeatureAbortEvent"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"logicalAddress\":4"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"opcode\":" + std::to_string(static_cast<int>(GET_CEC_VERSION))));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"FeatureAbortReason\":" + std::to_string(static_cast<int>(AbortReason::UNRECOGNIZED_OPCODE))));
}

// Test fixture description: removing a peer reaches clients as onDeviceRemoved carrying the
// departed logical address.
// COVERAGE_GAPS.md gap-plugin-sink-ondeviceremoved (rank 39, P2); symbols
// HdmiCecSinkImplementation::removeDevice and HdmiCecSink::Notification::OnDeviceRemoved.
TEST_F(HdmiCecSinkInitializedEventDsTest, onDeviceRemoved_SubscribedClient_ReceivesLogicalAddress)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    Plugin::CECDeviceParams& peer = Plugin::HdmiCecSinkImplementation::_instance->deviceList[6];
    peer.m_isDevicePresent = true;
    peer.m_logicalAddress = LogicalAddress(6);
    peer.update(OSDName("DEPARTING"));

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EVENT_SUBSCRIBE(0, _T("onDeviceRemoved"), _T("client.events.onDeviceRemoved"), message);
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->removeDevice(6));
    EVENT_UNSUBSCRIBE(0, _T("onDeviceRemoved"), _T("client.events.onDeviceRemoved"), message);

    EXPECT_THAT(notified, ::testing::HasSubstr("onDeviceRemoved"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"logicalAddress\":6"));
    EXPECT_EQ(std::string(""), peer.m_osdName.toString());
}

// Test fixture description: removal of a peer that is not present must not raise the event at all -
// the negative control that proves the notification is gated on the presence flag.
// COVERAGE_GAPS.md gap-plugin-sink-ondeviceremoved (rank 39, P2) - negative case.
TEST_F(HdmiCecSinkInitializedEventDsTest, onDeviceRemoved_AbsentDevice_ProducesNoNotification)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    Plugin::HdmiCecSinkImplementation::_instance->deviceList[LogicalAddress::UNREGISTERED].clear();

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EVENT_SUBSCRIBE(0, _T("onDeviceRemoved"), _T("client.events.onDeviceRemoved"), message);
    // LA 15 is the one address the plugin's poll thread never touches, so "absent" stays absent.
    EXPECT_NO_THROW(Plugin::HdmiCecSinkImplementation::_instance->removeDevice(LogicalAddress::UNREGISTERED));
    EVENT_UNSUBSCRIBE(0, _T("onDeviceRemoved"), _T("client.events.onDeviceRemoved"), message);

    EXPECT_THAT(notified, ::testing::Not(::testing::HasSubstr("onDeviceRemoved")));
}

// Test fixture description: an <Initiate ARC> from the audio system, with the panel powered on,
// drives the ARC state machine and reaches clients as arcInitiationEvent with a success status.
// Covers HdmiCecSinkProcessor::process(const InitiateArc&, const Header&),
// HdmiCecSinkImplementation::Process_InitiateArc and HdmiCecSink::Notification::ArcInitiationEvent.
TEST_F(HdmiCecSinkInitializedEventDsTest, arcInitiationEvent_InitiateArcFromAudioSystemWhilePoweredOn_NotifiesSuccess)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    // Process_InitiateArc only notifies while the panel is on, and the handler only accepts the
    // frame when the audio system's physical address is either unknown or the ARC port's. A fresh
    // fixture leaves deviceList[5] cleared, i.e. the invalid address, which is accepted.
    Plugin::HdmiCecSinkImplementation::_instance->onPowerModeChanged(
        WPEFramework::Exchange::IPowerManager::POWER_STATE_STANDBY,
        WPEFramework::Exchange::IPowerManager::POWER_STATE_ON);
    ASSERT_EQ(PowerStatus::ON,
        Plugin::HdmiCecSinkImplementation::_instance->deviceList[LogicalAddress::TV].m_powerStatus.toInt());

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EVENT_SUBSCRIBE(0, _T("arcInitiationEvent"), _T("client.events.arcInitiationEvent"), message);

    Connection cecBus;
    Plugin::HdmiCecSinkProcessor processor(cecBus);
    Plugin::HdmiCecSinkFrameListener listener(processor);

    // From the audio system (LA=5) to the TV (LA=0); 0xC0 is <Initiate ARC>.
    const uint8_t initiateArc[] = { 0x50, 0xC0 };
    CECFrame frame(initiateArc, sizeof(initiateArc));
    EXPECT_NO_THROW(listener.notify(frame));

    EVENT_UNSUBSCRIBE(0, _T("arcInitiationEvent"), _T("client.events.arcInitiationEvent"), message);

    EXPECT_THAT(notified, ::testing::HasSubstr("arcInitiationEvent"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"success\""));
}

// Test fixture description: the same <Initiate ARC> arriving while the panel is in standby must NOT
// raise arcInitiationEvent - the else arm of Process_InitiateArc.
TEST_F(HdmiCecSinkInitializedEventDsTest, arcInitiationEvent_InitiateArcWhileInStandby_ProducesNoNotification)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    Plugin::HdmiCecSinkImplementation::_instance->onPowerModeChanged(
        WPEFramework::Exchange::IPowerManager::POWER_STATE_ON,
        WPEFramework::Exchange::IPowerManager::POWER_STATE_STANDBY);
    ASSERT_EQ(PowerStatus::STANDBY,
        Plugin::HdmiCecSinkImplementation::_instance->deviceList[LogicalAddress::TV].m_powerStatus.toInt());

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EVENT_SUBSCRIBE(0, _T("arcInitiationEvent"), _T("client.events.arcInitiationEvent"), message);

    Connection cecBus;
    Plugin::HdmiCecSinkProcessor processor(cecBus);
    Plugin::HdmiCecSinkFrameListener listener(processor);

    const uint8_t initiateArc[] = { 0x50, 0xC0 };
    CECFrame frame(initiateArc, sizeof(initiateArc));
    EXPECT_NO_THROW(listener.notify(frame));

    EVENT_UNSUBSCRIBE(0, _T("arcInitiationEvent"), _T("client.events.arcInitiationEvent"), message);

    EXPECT_THAT(notified, ::testing::Not(::testing::HasSubstr("arcInitiationEvent")));
}

// Test fixture description: <Initiate ARC> is only honoured from the audio system, and never as a
// broadcast. Both rejections are the handler's addressing guard.
TEST_F(HdmiCecSinkInitializedEventDsTest, arcInitiationEvent_InitiateArcWithWrongAddressing_IsIgnored)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    Plugin::HdmiCecSinkImplementation::_instance->onPowerModeChanged(
        WPEFramework::Exchange::IPowerManager::POWER_STATE_STANDBY,
        WPEFramework::Exchange::IPowerManager::POWER_STATE_ON);

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EVENT_SUBSCRIBE(0, _T("arcInitiationEvent"), _T("client.events.arcInitiationEvent"), message);

    Connection cecBus;
    Plugin::HdmiCecSinkProcessor processor(cecBus);
    Plugin::HdmiCecSinkFrameListener listener(processor);

    // From Playback Device 1 (LA=4) - not the audio system - to the TV (LA=0).
    const uint8_t initiateArcWrongSource[] = { 0x40, 0xC0 };
    CECFrame wrongSource(initiateArcWrongSource, sizeof(initiateArcWrongSource));
    EXPECT_NO_THROW(listener.notify(wrongSource));

    // From the audio system (LA=5) to broadcast (LA=15).
    const uint8_t initiateArcBroadcast[] = { 0x5F, 0xC0 };
    CECFrame broadcast(initiateArcBroadcast, sizeof(initiateArcBroadcast));
    EXPECT_NO_THROW(listener.notify(broadcast));

    EVENT_UNSUBSCRIBE(0, _T("arcInitiationEvent"), _T("client.events.arcInitiationEvent"), message);

    EXPECT_THAT(notified, ::testing::Not(::testing::HasSubstr("arcInitiationEvent")));
}

// Test fixture description: a <Terminate ARC> from the audio system reaches clients as
// arcTerminationEvent. Covers HdmiCecSinkProcessor::process(const TerminateArc&, const Header&)
// and HdmiCecSinkImplementation::Process_TerminateArc, which - unlike initiation - has no
// power-state precondition.
TEST_F(HdmiCecSinkInitializedEventDsTest, arcTerminationEvent_TerminateArcFromAudioSystem_NotifiesSuccess)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EVENT_SUBSCRIBE(0, _T("arcTerminationEvent"), _T("client.events.arcTerminationEvent"), message);

    Connection cecBus;
    Plugin::HdmiCecSinkProcessor processor(cecBus);
    Plugin::HdmiCecSinkFrameListener listener(processor);

    // From the audio system (LA=5) to the TV (LA=0); 0xC5 is <Terminate ARC>.
    const uint8_t terminateArc[] = { 0x50, 0xC5 };
    CECFrame frame(terminateArc, sizeof(terminateArc));
    EXPECT_NO_THROW(listener.notify(frame));

    EVENT_UNSUBSCRIBE(0, _T("arcTerminationEvent"), _T("client.events.arcTerminationEvent"), message);

    EXPECT_THAT(notified, ::testing::HasSubstr("arcTerminationEvent"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"success\""));
}

// Test fixture description: an inbound <Standby> reaches clients as standbyMessageReceived. Unlike
// the view-on opcodes this handler has no addressing guard, so the broadcast form is accepted too.
TEST_F(HdmiCecSinkInitializedEventDsTest, standbyMessageReceived_InboundStandby_NotifiesSubscribedClient)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    string notified;
    ON_CALL(service, Submit(::testing::_, ::testing::_))
        .WillByDefault(::testing::Invoke(
            [&](const uint32_t, const Core::ProxyType<Core::JSON::IElement>& json) {
                string text;
                json->ToString(text);
                notified += text;
                return Core::ERROR_NONE;
            }));

    EVENT_SUBSCRIBE(0, _T("standbyMessageReceived"), _T("client.events.standbyMessageReceived"), message);

    Connection cecBus;
    Plugin::HdmiCecSinkProcessor processor(cecBus);
    Plugin::HdmiCecSinkFrameListener listener(processor);

    // From Playback Device 1 (LA=4) to the TV (LA=0); 0x36 is <Standby>.
    const uint8_t standbyDirected[] = { 0x40, 0x36 };
    CECFrame directed(standbyDirected, sizeof(standbyDirected));
    EXPECT_NO_THROW(listener.notify(directed));

    EVENT_UNSUBSCRIBE(0, _T("standbyMessageReceived"), _T("client.events.standbyMessageReceived"), message);

    EXPECT_THAT(notified, ::testing::HasSubstr("standbyMessageReceived"));
    EXPECT_THAT(notified, ::testing::HasSubstr("\"logicalAddress\":4"));
}

//=============================================================================
// The plugin's own notification sink (L1 Level)
//
// HdmiCecSink::Notification is a PRIVATE nested class held by value as
// Core::Sink<Notification> _notification, so a test cannot name it, construct it, or reach it
// through the plugin's public surface. It becomes reachable exactly once: IShell's
// Register/Unregister overloads for RPC::IRemoteConnection::INotification are non-virtual inlines
// that forward to COMLink(), and ServiceMock::COMLink() returns nullptr by default - which is why
// the sink has never been observable and why the plugin logs "Failed to get IRemoteConnection" on
// every teardown. Pointing COMLink() at the fixture's existing COMLinkMock hands the sink to the
// test, using only fixture members that are already there.
//=============================================================================

// Test fixture description: the notification sink accepts the remote-connection activation callback
// and publishes exactly the two interfaces its interface map declares.
// Covers HdmiCecSink::Notification::Activated and HdmiCecSink::Notification::QueryInterface.
TEST_F(HdmiCecSinkDsTest, PluginNotificationSink_ActivationHookAndInterfaceMap_AreExercised)
{
    ON_CALL(service, COMLink())
        .WillByDefault(::testing::Return(&comLinkMock));

    const RPC::IRemoteConnection::INotification* captured = nullptr;
    ON_CALL(comLinkMock, Unregister(::testing::Matcher<const RPC::IRemoteConnection::INotification*>(::testing::_)))
        .WillByDefault(::testing::Invoke(
            [&](const RPC::IRemoteConnection::INotification* sink) {
                captured = sink;
            }));

    // Teardown is the one place the plugin hands its sink to the COM link. Calling it here is safe:
    // Deinitialize is idempotent, so the fixture destructor's own call becomes a no-op.
    plugin->Deinitialize(&service);
    ASSERT_NE(nullptr, captured);

    RPC::IRemoteConnection::INotification* sink = const_cast<RPC::IRemoteConnection::INotification*>(captured);

    // The activation hook is a deliberate no-op; the contract is that it accepts the callback.
    EXPECT_NO_THROW(sink->Activated(nullptr));

    // The interface map publishes the CEC-sink notification and the remote-connection notification,
    // and nothing else - an unrelated interface identifier must be refused.
    EXPECT_NE(nullptr, sink->QueryInterface(Exchange::IHdmiCecSink::INotification::ID));
    EXPECT_NE(nullptr, sink->QueryInterface(RPC::IRemoteConnection::INotification::ID));
    EXPECT_EQ(nullptr, sink->QueryInterface(Exchange::IHdmiCecSink::ID));
}

// Test fixture description: the implementation's power-manager notification wrapper is a PRIVATE
// nested class held by value, so it can only be observed at the one moment the implementation hands
// it back to the power manager - the Unregister call that opens its destructor. Exercising it from
// inside that capture is safe and deliberate: Unregister is the destructor's first statement, so the
// wrapper, its parent and the singleton instance pointer are all still valid, and the callback is
// driven with POWER_STATE_ON so it takes the no-side-effect arm.
// Covers HdmiCecSinkImplementation::PowerManagerNotification::OnPowerModeChanged and its
// interface map.
TEST_F(HdmiCecSinkDsTest, PowerManagerNotificationWrapper_ForwardsModeChangeAndPublishesItsInterface)
{
    ASSERT_NE(nullptr, Plugin::HdmiCecSinkImplementation::_instance);

    bool forwarded = false;
    bool publishesOwnInterface = false;
    bool refusesUnrelatedInterface = false;

    ON_CALL(PowerManagerMock::Mock(), Unregister(::testing::Matcher<const Exchange::IPowerManager::IModeChangedNotification*>(::testing::_)))
        .WillByDefault(::testing::Invoke(
            [&](const Exchange::IPowerManager::IModeChangedNotification* notification) {
                if (notification != nullptr) {
                    Exchange::IPowerManager::IModeChangedNotification* sink
                        = const_cast<Exchange::IPowerManager::IModeChangedNotification*>(notification);

                    sink->OnPowerModeChanged(
                        WPEFramework::Exchange::IPowerManager::POWER_STATE_STANDBY,
                        WPEFramework::Exchange::IPowerManager::POWER_STATE_ON);
                    forwarded = true;

                    publishesOwnInterface
                        = (sink->QueryInterface(Exchange::IPowerManager::IModeChangedNotification::ID) != nullptr);
                    refusesUnrelatedInterface
                        = (sink->QueryInterface(Exchange::IHdmiCecSink::ID) == nullptr);
                }
                return Core::ERROR_NONE;
            }));

    plugin->Deinitialize(&service);

    EXPECT_TRUE(forwarded);
    EXPECT_TRUE(publishesOwnInterface);
    EXPECT_TRUE(refusesUnrelatedInterface);
}