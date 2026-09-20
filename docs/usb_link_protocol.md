# Earlier USB Link

I kept this document with the historical firmware branch. This USB implementation offers discovery, status and a switch to mass-storage access. It pairs with the [earlier Windows client](https://github.com/zjmdong/Day_Distiller/tree/old/windows-dev-client-py).

The older serial interface places the protocol and logs on separate CDC ports. For a new application, start with the [current firmware](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) and [desktop app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app).

See [usb_link.h](../main/include/usb_link.h) for the interface available in this snapshot.
