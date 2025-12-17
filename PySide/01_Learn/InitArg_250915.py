# namespace CGM_250901{
#         const Params params = {
#             DAC_Channel_2,          // PA5/VR   DAC_Chan_RE
#             DAC_Channel_1,          // PA4/VS   DAC_Chan_WE
#             3,                      // nbr of channels
#             {
#                 // PA1 20400
#                 AD_ChanParams(To_uint8(ADC_GPIO::PA1), 20400, ADC_SampleTime_55Cycles5, GainMode::RES_VALUE, VsMode::STATIC),
#                 // PC0 4700
#                 AD_ChanParams(To_uint8(ADC_GPIO::PA6), 4700, ADC_SampleTime_55Cycles5, GainMode::RES_VALUE, VsMode::STATIC),
#                 // PC1 200
#                 AD_ChanParams(To_uint8(ADC_GPIO::PA7), 200, ADC_SampleTime_55Cycles5, GainMode::RES_VALUE, VsMode::STATIC),
#             }
#         };
#     }