import asyncio
import time

from pycyphal.application import make_node, NodeInfo
from pycyphal.transport.can import CANTransport
from pycyphal.transport.can.media.socketcan import SocketCANMedia
from pycyphal.presentation import ServiceRequestMetadata

# fc.__init__ ensures dsdl_out is on sys.path
import fc  # noqa: F401

from fc.config import (
    CAN_INTERFACE,
    CAN_MTU,
    EFM_DSDL,
    NODE_ID_CUTDOWN,
    NODE_NAME_CUTDOWN,
    RADIO_CUTDOWN_DSDL,
    RELAY_DSDL,
    PORT_RELAY,
    PORT_RADIO_CUTDOWN,
    NODE_ID_SENSOR,
    NODE_ID_POWER,
    VARAIBLE_VOLTAGE_DSDL,
    PORT_VARIABLE_VOLTAGE,
    resolve_dsdl_class,
)


RadioCutdown_0_1 = resolve_dsdl_class(RADIO_CUTDOWN_DSDL)
Relays_1_0 = resolve_dsdl_class(RELAY_DSDL)
VariableVoltage_0_1 = resolve_dsdl_class(VARAIBLE_VOLTAGE_DSDL)

# ---------------------------------------------------------------------------
# Main service loop
# ---------------------------------------------------------------------------

async def run() -> None:
    # ── Cyphal node ──────────────────────────────────────────────────────
    media = SocketCANMedia(CAN_INTERFACE, mtu=CAN_MTU)
    transport = CANTransport(media=media, local_node_id=NODE_ID_CUTDOWN)
    node = make_node(
        info=NodeInfo(name=NODE_NAME_CUTDOWN),
        transport=transport,
    )
    node.start()
        

    tasks: list[asyncio.Task] = []
    cutdown_srv = node.get_server(RadioCutdown_0_1, PORT_RADIO_CUTDOWN)
    cutdown_client = node.make_client(Relays_1_0, NODE_ID_SENSOR, PORT_RELAY)
    voltage_client = node.make_client(VariableVoltage_0_1, NODE_ID_POWER, PORT_VARIABLE_VOLTAGE)

    async def _cutdown_callback(
        request: RadioCutdown_0_1.Request,
        metadata: ServiceRequestMetadata,
    ) -> RadioCutdown_0_1.Response:
        """Handle incoming cutdown service requests"""
        print(f"Received cutdown request from node {metadata.client_node_id}!")

        voltage_req = VariableVoltage_0_1.Request(
            wiper_code=VariableVoltage_0_1.Request.V1_25
        )

        result = await voltage_client.call(voltage_req)

        if result is None:
            print("Variable voltage request timed out (no response from server).")
        else:
            response, _metadata = result
            if response.status == VariableVoltage_0_1.Response.OK:
                print("Success: All relays enabled for 1 second.")
            elif response.status == VariableVoltage_0_1.Response.status:
                print("Error: Variable voltage command failed")
            else:
                print(f"Error: Variable voltage command failed (status={response.status}).")
 

        relay_req = Relays_1_0.Request(
            relay_index=Relays_1_0.Request.ALL,
            enabled=True,                 
            auto_off_after_ms=1000,
        )

        await asyncio.sleep(5)
        result = await cutdown_client.call(relay_req)
        await asyncio.sleep(1)

        if result is None:
            print("Relays request timed out (no response from server).")
        else:
            response, _metadata = result
            if response.status == Relays_1_0.Response.OK:
                print("Success: All relays enabled for 1 second.")
            elif response.status == Relays_1_0.Response.INVALID_INDEX:
                print("Error: Invalid relay index.")
            elif response.status == Relays_1_0.Response.INVALID_DURATION:
                print("Error: Invalid auto-off duration.")
            else:
                print(f"Error: Relay command failed (status={response.status}).")

        voltage_req = VariableVoltage_0_1.Request(
            wiper_code=VariableVoltage_0_1.Request.V5_04
        )

        result = await voltage_client.call(voltage_req)

        if result is None:
            print("Variable voltage request timed out (no response from server).")
        else:
            response, _metadata = result
            if response.status == VariableVoltage_0_1.Response.OK:
                print("Success: All relays enabled for 1 second.")
            elif response.status == VariableVoltage_0_1.Response.status:
                print("Error: Variable voltage command failed")
            else:
                print(f"Error: Variable voltage command failed (status={response.status}).")
 


        return RadioCutdown_0_1.Response()

    cutdown_srv.serve_in_background(_cutdown_callback)

    tasks: list[asyncio.Task] = []
    tasks.append(asyncio.create_task(asyncio.Event().wait()))

    try:
        await asyncio.gather(*tasks)
    finally:
        cutdown_srv.close()
        node.close()


if __name__ == "__main__":
    asyncio.run(run())
