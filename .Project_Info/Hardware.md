主控：ESP32S3-WROOM-1-N16R8
摄像头：OV5640
IMU：LSM6DS3TR-C
麦克风：MSM261S4030H0R
RTC时钟：PCF8563T
电量计：MAX17048G+T10
其他外设：TF卡、3颗WS2812B RGB灯(串联)

电源网络：
TPS63020 DCDC + 摄像头2.8v&1.5v LDO
电量计、RTC时钟直连电池，不受开关控制，保持常开

I2C分配：
I2C0：连接电量计、IMU、RTC时钟
I2C1：连接摄像头OV5640

IO分配：
IO19 -- USB_D-
IO20 -- USB_D+
IO8 -- I2C0_SDA
IO9 -- I2C0_SCL
IO4 -- IMU_INT(IMU 的中断唤醒线。连接 LSM6DS3TR-C 的 INT1)
IO47 -- TF_CS(TF 卡片选)
IO1 -- TF_MOSI(TF 卡主出从入)
IO2 -- TF_MISO(TF 卡主入从出)
IO48 -- TF_SCK(TF 卡时钟)
IO40 -- MIC_SCK(麦克风的串行时钟线)
IO41 -- MIC_WS(麦克风的帧同步)
IO38 -- MIC_SD(麦克风的数据输入线)
IO42 -- RGB_DIN(WS2812B数据线)
IO3 -- CAM_RST(摄像头RST)
IO6 -- I2C1_SDA(摄像头I2C)
IO7 -- I2C1_SCL(摄像头I2C)
IO15 -- CAM_MCLK(摄像头MCLK)
IO16 -- CAM_PCLK(摄像头PCLK)
IO17 -- CAM_VSYNC(摄像头VSYNC)
IO18 -- CAM_HREF(摄像头HREF)
IO10 -- CAM_D0(摄像头D0)
IO11 -- CAM_D1(摄像头D1)
IO12 -- CAM_D2(摄像头D2)
IO13 -- CAM_D3(摄像头D3)
IO14 -- CAM_D4(摄像头D4)
IO21 -- CAM_D5(摄像头D5)
IO39 -- CAM_D6(摄像头D6)
IO45 -- CAM_D7(摄像头D7)

备注：
1.麦克风的L/R声道选择引脚接地，所以固定在左声道输出数据。
