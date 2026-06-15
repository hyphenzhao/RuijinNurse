#!/bin/sh

dir=`dirname $0`
. ${dir}/userconfig.sh

rtsold_pid="/var/run/rtsold.sh.pid"
major_version=`uname -a | sed 's/^.*Darwin Kernel Version \([0-9]*\).*$/\1/'`

if [ $# -ne 1 ]; then
  echo ./`basename $0` \"your ipv4 attached interface\(i.e. en0 or en1\)\"
  exit 1
fi

kextloaded=`kextstat -l -b org.momose.kext.isatap`
if [ "X${kextloaded}" = "X" ]; then
  # kext isn't loaded yet
  kextload ${dir}/isatap.kext
fi


interface=$1

v4addr=`${ifconfig} ${interface} | grep 'inet ' | awk '{print $2;}'`
echo $v4addr
if [ "X${v4addr}" = "X" ]; then
  echo "Couldn't find IPv4 address"
  exit 1
fi
v6isataplinklocaladdr=`${ifconfig} ist0 | grep 'inet6 fe80' | cut -f 2 -d ' '`
setif=false
if [ "x${v6isataplinklocaladdr}" == "x" ]; then
  setif=true
else
  v4isatap=`expr ${v6isataplinklocaladdr} : '.*5efe:\(.*\)%ist0'`
#  add1=`expr $v4isatap : '\(.*\):.*' | tr '[a-f]' '[A-F]'`
#  add2=`expr $v4isatap : '.*:\(.*\)' | tr '[a-f]' '[A-F]'`
#  a1=`echo "ibase=16; ${add1} / 100" | bc`
#  a2=`echo "ibase=16; ${add1} % 100" | bc`
#  a3=`echo "ibase=16; ${add2} / 100" | bc`
#  a4=`echo "ibase=16; ${add2} % 100" | bc`
#  extractedaddr=${a1}.${a2}.${a3}.${a4}
   extractedaddr=${v4isatap}
  if [ "x${v4addr}" != "x${extractedaddr}" ]; then
    setif=true
    ndp -P
    ndp -R
    ${ifconfig} ist0 inet6 ${v6isataplinklocaladdr} prefixlen 64 delete
  fi
fi

if [ $setif ]; then
  ${ifconfig} ist0 inet6 fe80::5efe:${v4addr} prefixlen 64
  if [ ${major_version} = "11" ]; then
    # A workaround to work on Lion
    ndp -I ist0
    # Temporary address shouldn't be used
    sysctl -w net.inet6.ip6.use_tempaddr=0
  fi
  if [ -f $rtsold_pid ]; then
    kill -2 -`cat $rtsold_pid`
  fi
fi

