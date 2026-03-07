#!/usr/bin/expect

# Set variables for OLT IP, username, and password
set olt_ip [lindex $argv 0]
set username "bifrost"
set password "acaidosdeuses%gabisat"

# Set timeout to prevent infinite waiting
set timeout 20

# Start telnet session
spawn telnet $olt_ip

# Wait for the Username prompt and send the username
expect "Username:"
send "$username\r"

# Wait for the Password prompt and send the password
expect "Password:"
send "$password\r"

# Wait for the EPON> prompt indicating successful login
expect "EPON>"

# Enter privileged mode
send "enable\r"
expect "EPON#"

# Initialize an empty list to store the ONU information
set onu_list {}
set power_list {}

# Iterate over 4 interfaces (0/1, 0/2, 0/3, 0/4)
for {set interface 1} {$interface <= 4} {incr interface} {
    # Send command to get ONU information for the interface
    send "show onu info epon 0/$interface all\r"

    # Expect the output for each interface
    expect {
        -re {([0-9]+/[0-9]+):([0-9]+)\s+([0-9a-fA-F:]+)\s+(Up|Down)} {
            # Capture Interface, ONU ID, MAC address, and Status
            set interface_num $interface
            set onu_id $expect_out(2,string)
            set mac_address $expect_out(3,string)
            set status $expect_out(4,string)

            # Append only "Up" ONUs to the list for further power check
            if {$status == "Up"} {
                lappend onu_list [list "0/$interface_num" $onu_id $mac_address]
            }

            exp_continue
        }
        -re "Enter Key To Continue" {
            # If "Enter key to continue" prompt appears, send a space to continue
            send " "
            exp_continue
        }
        "EPON#" {
            # End loop for current interface when the prompt is reached
            continue
        }
    }
}

# Second loop to get the power information for ONUs that are 'Up'
foreach onu $onu_list {
    set interface [lindex $onu 0]
    set onu_id [lindex $onu 1]
    set mac_address [lindex $onu 2]

    # Send command to get receive power information for each ONU
    send "show onu optical-ddm epon $interface $onu_id\r"
    expect {
        -re {RxPower.*(\-[0-9]+\.[0-9]+)\s+dBm} {
            set power $expect_out(1,string)
            lappend power_list [list $interface $onu_id $mac_address $power]
        }
        -re "Enter Key To Continue" {
            send " "
            exp_continue
        }
        timeout {
            puts "Timeout occurred while getting power info for ONU ID: $onu_id"
        }
    }

    # Wait for 0.5 seconds before sending the next command
    exec sleep 0.5
}

# Close the telnet session
send "exit\r"
send "exit\r"
expect eof

# Separate ONUs by PON and sort each group by power (from worst to best)
set pon_groups [dict create]
foreach power_info $power_list {
    set interface [lindex $power_info 0]
    set onu_id [lindex $power_info 1]
    set mac_address [lindex $power_info 2]
    set power [lindex $power_info 3]

    # Ensure the PON key exists in the dictionary
    if {![dict exists $pon_groups $interface]} {
        dict set pon_groups $interface {}
    }

    # Append ONU data to the corresponding PON group
    dict set pon_groups $interface [concat [dict get $pon_groups $interface] [list [list $onu_id $mac_address $power]]]
}

# Print sorted ONU power information by PON
puts "\nCollected ONU Power Information (Sorted by PON, Worst to Best):"
puts "==============================================================="

# Iterate over each PON and sort the ONUs inside
foreach {pon onu_group} [dict get $pon_groups] {
    puts "PON $pon:"
    set sorted_onu_group [lsort -real -index 2 $onu_group]

    foreach onu $sorted_onu_group {
        set onu_id [lindex $onu 0]
        set mac_address [lindex $onu 1]
        set power [lindex $onu 2]
        
        puts "ONU ID: $onu_id, MAC: $mac_address, Receive Power: $power dBm"
    }
    
    # Print a separator between PONs
    puts "---------------------------------------------------------------"
}