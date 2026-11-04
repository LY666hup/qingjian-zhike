# The Command Lines that defined the parameter attributes.

set_param -param_id {H_ActiveSize} -data_type {Integer} -min {-2147483648} -max {2147483647}
set_param -param_id {H_FrameSize} -data_type {Integer} -min {-2147483648} -max {2147483647}
set_param -param_id {H_SyncStart} -data_type {Integer} -min {-2147483648} -max {2147483647}
set_param -param_id {H_SyncEnd} -data_type {Integer} -min {-2147483648} -max {2147483647}
set_param -param_id {V_ActiveSize} -data_type {Integer} -min {-2147483648} -max {2147483647}
set_param -param_id {V_FrameSize} -data_type {Integer} -min {-2147483648} -max {2147483647}
set_param -param_id {V_SyncStart} -data_type {Integer} -min {-2147483648} -max {2147483647}
set_param -param_id {V_SyncEnd} -data_type {Integer} -min {-2147483648} -max {2147483647}
# The Tcl Procs that used in parameter dependency.

proc updateRelValue { value } {
 set tmp $value
 return $tmp
}