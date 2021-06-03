#!/usr/bin/env python

"""
Fixes a MySQL dump made with the right format so it can be directly
imported to a new PostgreSQL database.

Dump using:
mysqldump --compatible=postgresql --default-character-set=utf8 -r databasename.mysql -u root databasename
mysqldump --opt --compatible=postgresql --default-character-set=utf8 -d databasename -r dumpfile.sql -u root -p
"""

import re
import sys
import os
import time
import subprocess


def parse(input_filename, output_filename):
    """Feed it a file, and it'll output a fixed one"""

    # State storage
    if input_filename == "-":
        num_lines = -1
    else:
        num_lines = int(subprocess.check_output(["wc", "-l", input_filename]).strip().split()[0])
    tables = {}
    current_table = None
    creation_lines = []
    cast_lines = []
    index_lines = []
    comment_line = []
    num_inserts = 0
    started = time.time()
    primary_key = None

    # Open output file and write header. Logging file handle will be stdout
    # unless we're writing output to stdout, in which case NO PROGRESS FOR YOU.
    if output_filename == "-":
        output = sys.stdout
        logging = open(os.devnull, "w")
    else:
        output = open(output_filename, "w")
        logging = sys.stdout

    if input_filename == "-":
        input_fh = sys.stdin
    else:
        input_fh = open(input_filename)

    output.write("-- Converted by db_converter\n")
    output.write("START TRANSACTION;\n")
    # output.write("SET standard_conforming_strings=off;\n")
    # output.write("SET escape_string_warning=off;\n")
    # output.write("SET CONSTRAINTS ALL DEFERRED;\n\n")

    for i, line in enumerate(input_fh):
        time_taken = time.time() - started
        percentage_done = (i + 1) / float(num_lines)
        secs_left = (time_taken / percentage_done) - time_taken
        logging.write("\rLine %i (of %s: %.2f%%) [%s tables] [%s inserts] [ETA: %i min %i sec]" % (
            i + 1,
            num_lines,
            ((i + 1) / float(num_lines)) * 100,
            len(tables),
            num_inserts,
            secs_left // 60,
            secs_left % 60,
        ))
        logging.flush()
        # line = line.decode("utf8").strip().replace(r"\\", "WUBWUBREALSLASHWUB").replace(r"\'", "''").replace("WUBWUBREALSLASHWUB", r"\\")
        line = line.encode('utf-8').decode('utf8').strip().replace(r"\\", "WUBWUBREALSLASHWUB").replace(r"\'",
                                                                                                        "''").replace(
            "WUBWUBREALSLASHWUB", r"\\")
        # Ignore comment lines
        if line.startswith("--") or line.startswith("/*") or line.startswith("LOCK TABLES") or line.startswith(
                "DROP TABLE") or line.startswith("UNLOCK TABLES") or not line:
            continue

        # Outside of anything handling
        if current_table is None:
            # Start of a table creation statement?
            if line.startswith("CREATE TABLE"):
                current_table = line.split('"')[1].lower()
                tables[current_table] = {"columns": []}
                creation_lines = []
            else:
                print("\n ! Unknown line in main body: %s" % line)

        # Inside-create-statement handling
        else:
            # Is it a column?
            if line.startswith('"'):
                useless, name, definition = line.strip(",").split('"', 2)
                name = name.lower()
                try:
                    type, extra = definition.strip().split(" ", 1)

                except ValueError:
                    type = definition.strip()
                    extra = ""
                extra = re.sub("CHARACTER SET [\w\d]+\s*", "", extra)
                extra = re.sub("COLLATE [\w\d]+\s*", "", extra.replace("'0000-00-00 00:00:00'", "NULL"))
                if extra.find("COMMENT '") > -1:
                    pattern = re.compile("COMMENT '(.*)'")
                    comment_line.append(
                        u"COMMENT ON COLUMN \"%s\".\"%s\" is '%s'" % (current_table, name, pattern.findall(extra)[0]))
                    extra = re.sub("COMMENT '.*'", "", extra)

                # See if it needs type conversion
                final_type = None
                if type.startswith("tinyint("):
                    type = "smallint"
                elif type.startswith("int(") and extra.startswith("unsigned"):
                    type = "bigint"
                elif type.startswith("int("):
                    type = "integer"
                elif type.startswith("smallint(") and extra.startswith("unsigned"):
                    type = "integer"
                elif type.startswith("smallint("):
                    type = "smallint"
                elif type.startswith("mediumint("):
                    type = "integer"
                elif type.startswith("bigint("):
                    type = "bigint"
                elif type.startswith("year"):
                    type = "integer"

                elif type == "longtext":
                    type = "text"
                elif type == "mediumtext":
                    type = "text"
                elif type == "tinytext":
                    type = "text"
                elif type.startswith("varchar("):
                    size = int(type.split("(")[1].split(")")[0])
                    type = "varchar(%s)" % (size * 2)

                elif type == "datetime":
                    type = "timestamp without time zone"
                elif type == "timestamp":
                    type = "timestamp with time zone"

                elif type == "double":
                    type = "double precision"
                elif type.startswith("float"):
                    type = "real"
                elif type.endswith("blob"):
                    type = "bytea"
                elif type.endswith("binary"):
                    type = "bytea"
                elif type.startswith("enum(") or type.startswith("set("):
                    type = "varchar"

                elif type.startswith("linestring"):
                    type = "path"
                elif type.startswith("point"):
                    type = "point"

                extra = extra.replace("unsigned", "")

                if final_type:
                    cast_lines.append(
                        "ALTER TABLE \"%s\" ALTER COLUMN \"%s\" DROP DEFAULT, ALTER COLUMN \"%s\" TYPE %s USING CAST(\"%s\" as %s)" % (
                        current_table, name, name, final_type, name, final_type))
                creation_lines.append('"%s" %s %s' % (name, type, extra))
                tables[current_table]['columns'].append((name, type, extra))
            # Is it a constraint or something?
            elif line.startswith("PRIMARY KEY"):
                creation_lines.append(line.rstrip(",").lower())
                primary_key = line.split("(")[1].split(")")[0].lower()
            elif line.startswith("KEY"):
                index_lines.append("CREATE INDEX %s on \"%s\" (%s)" % (
                line.split(" ")[1], current_table, line.split("(")[1].split(")")[0].lower()))
            # Is it the end of the table?
            elif line == ");" or line == ")":
                output.write("CREATE TABLE \"%s\" (\n" % current_table)
                for i, line in enumerate(creation_lines):
                    output.write("    %s%s\n" % (line, "," if i != (len(creation_lines) - 1) else ""))
                if primary_key is not None:
                    output.write(') distributed by (%s);\n\n' % primary_key)
                else:
                    output.write(') distributed by ();\n\n')
                current_table = None
                primary_key = None
            # ???
            else:
                print("\n ! Unknown line inside table creation: %s" % line)

    # Finish file
    output.write("\n-- Post-data save --\n")
    output.write("\n-- Add Comment --\n")

    for line in comment_line:
        output.write(u"%s;\n" % line)

    output.write("COMMIT;\n\n")

    # Write index out
    # output.write("START TRANSACTION;\n")
    # output.write("\n-- Index --\n")
    # for line in index_lines:
    #     output.write("%s;\n" % line)
    #
    # # Finish file
    # output.write("\n")
    # output.write("COMMIT;\n")
    print("")


if __name__ == "__main__":
    parse(sys.argv[1], sys.argv[2])
