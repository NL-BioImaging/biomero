#!/bin/bash
#(trap 'kill 0' SIGINT;
echo 'pulling images';
$pullcommands
echo 'script done, tail `sing.log` for background progress'
#)