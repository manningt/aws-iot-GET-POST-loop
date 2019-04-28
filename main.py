import argparse
parser = argparse.ArgumentParser(description='Start AWS thing get/update loop')
parser.add_argument('-l','--loglevel', default="INFO", help='Log-level, e.g. INFO, DEBUG', required=False)
parser.add_argument('-p','--protocol', default="HTTPS", help='MQTT or HTTPS (default)', required=False)
args = parser.parse_args()

import logging
logging.basicConfig(level=getattr(logging, args.loglevel))
#logging.basicConfig(level=logging.DEBUG)

import aws_thing_loop
aws_thing_loop.main(thing_type='Signal', protocol=args.protocol)
