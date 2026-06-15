#!/bin/sh

dir=`dirname $0`
. ${dir}/userconfig.sh

prlname=isatap
dig=/usr/bin/dig  # BIND9's dig

function usage()
{
  echo "update-isataprtr.sh <your domain>"
}

if [ $# -ne 1 ]; then
  usage
fi
domain=$1

#internal variables; don't edit
curprl=""
oldprl=""
newprl=""

newprl=`${dig} +short +domain=$domain +search $prlname a | grep '^[0-9.]*$'`
oldprl=`${ifconfig} ist0 | grep isataprtr | awk '{print $2}'`

for r in $newprl; do
# case 1. has already appeared in the new list (i.e. already exists
#         in the current list) -> do nothing
        found=`echo "find-isataprrtr $curprl" | grep $r`
        if [ "X$found" != X ]; then
                continue;
        fi

# case 2. already exists in the old list -> update the old list
        found=`echo "find-isataprrtr $oldprl" | grep $r`
        if [ "X$found" != X ]; then
                oldprl=`echo $oldprl | sed s/$r//`
                continue;
        fi

# case 3. otherwise -> write down the new isataprtr and update
#         the old list and the current list.
        ${ifconfig} ist0 isataprtr $r
        curprl="$r $curprl"
        oldprl=`echo $oldprl | sed s/$r//`
        continue;
done

for r in $oldprl; do
        ${ifconfig} ist0 deleteisataprtr $r
done
