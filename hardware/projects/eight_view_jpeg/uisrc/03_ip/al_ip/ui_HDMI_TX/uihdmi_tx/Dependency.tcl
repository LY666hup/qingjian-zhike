# The Command Lines that defined the parameter attributes.

set_param -param_id {DEVICE} -data_type {String}
# The Tcl Procs that used in parameter dependency.

proc updateRelValue { value } {
 set tmp $value
 return $tmp
}