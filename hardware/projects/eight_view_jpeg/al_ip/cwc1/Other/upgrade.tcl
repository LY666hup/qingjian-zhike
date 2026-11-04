#! /usr/bin/tclsh

proc report_ip_status { current_ip_version original_ip_version upgrade_result current_part recommended_part } {

    set $current_ip_version [string trim $current_ip_version]
    set recommend_version "1.0.1"
    set recommend_version_list [split $recommend_version "."]
    set current_ip_version_list [split $current_ip_version "."]
    set Upgradable 1
    set Lock 1

    if {[lindex $recommend_version_list 0] != [lindex $current_ip_version_list 0]} {
        set IP_Status 0
        set Recommendation 0
    } elseif {[lindex $recommend_version_list 1] != [lindex $current_ip_version_list 1]} {
        set IP_Status 1
        set Recommendation 0
    } elseif {[lindex $recommend_version_list 2] != [lindex $current_ip_version_list 2]} {
        set IP_Status 2
        set Recommendation 0
    } else {
        set IP_Status 4
        set Recommendation 2
        set Upgradable 0
        set Lock 0
    }

    return [list $IP_Status $Recommendation $Lock $Upgradable]
}

proc upgrade_ip	{ param_txt_path port_txt_path current_ip_version } {
    return 1 
}
