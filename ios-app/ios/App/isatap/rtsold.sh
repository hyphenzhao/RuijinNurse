#!/bin/sh

dir=`dirname $0`
. ${dir}/userconfig.sh

interface=ist0

echo $$ > /var/run/rtsold.sh.pid

trap "echo 'send rtsol'; ${rtsol} $interface 2> /dev/null" 2
trap 'rm /var/run/rtsold.sh.pid; ' EXIT

while true; do
  ${rtsol} $interface 2> /dev/null
  duration=`ndp -rn | sed 's/.*expire=\(.*\)m\(.*\)s$/\1 * 60 + \2 - 10/' | bc`
  if [ "x${duration}" = "x" ]; then
    duration=3600
  elif [ ${duration} -eq 0 ]; then
    duration=3600
  fi
  sleep $duration
done
