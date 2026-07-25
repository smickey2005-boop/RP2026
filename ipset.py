sudo nmcli con mod a113544d-64e9-3104-be59-b4e7e3466420 ipv4.method auto
sudo nmcli con mod a113544d-64e9-3104-be59-b4e7e3466420 ipv4.addresses ""
sudo nmcli con mod a113544d-64e9-3104-be59-b4e7e3466420 ipv4.gateway ""
sudo nmcli con mod a113544d-64e9-3104-be59-b4e7e3466420 ipv4.dns ""
sudo nmcli con up a113544d-64e9-3104-be59-b4e7e3466420
