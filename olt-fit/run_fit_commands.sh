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

# Run commands for each interface
for {set interface 1} {$interface <= 4} {incr interface} {
    send "show onu info epon 0/$interface all\r"

    expect {
        -re "Enter Key To Continue" {
            send " "
            exp_continue
        }
        "EPON#" {
            # Output will be automatically sent to stdout due to the spawn command, which is captured by run_all.sh
        }
    }
}

# Close the telnet session
send "exit\r"
expect eof