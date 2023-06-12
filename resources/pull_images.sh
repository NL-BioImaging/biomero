#/bin/bash
(trap 'kill 0' SIGINT;
echo 'pulling images';
$pullcommands
wait)