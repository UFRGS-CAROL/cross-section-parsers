#!/usr/bin/env python3

import sys
import csv
from datetime import timedelta
from datetime import datetime

# smallest run in minutes
SMALLEST_RUN = timedelta(minutes=20)
ONE_HOUR = 60


def get_dt(year_date, day_time, sec_frac):
    yv = year_date.split('/')
    day = int(yv[0])
    month = int(yv[1])
    year = int(yv[2])

    dv = day_time.split(':')
    hour = int(dv[0])
    minute = int(dv[1])
    second = int(dv[2])

    # we get secFrac in seconds, so we must convert to microseconds
    # e.g: 0.100 seconds = 100 milliseconds = 100000 microseconds
    microsecond = int(float(sec_frac) * 1e6)

    return datetime(year, month, day, hour, minute, second, microsecond)


def read_count_file(in_file_name):
    """
    Read neutron log file
    :param in_file_name: neutron log filename
    :return: list with all neutron lines
    """
    file_lines = []
    with open(in_file_name, 'r') as in_file:
        for l in in_file:
            # Sanity check, we require a date at the beginning of the line
            line = l.rstrip().split()
            if len(line) < 7:
                print(f"Ignoring line (malformed):{l}")
                continue

            if "N/A" in l:
                break

            file_lines.append(line)
    return file_lines


def get_fluency_flux(start_dt, end_dt, file_lines, factor, distance_factor):
    # inFile = open(inFileName, 'r')
    # endDT = startDT + timedelta(minutes=60)

    # last_counter_20 = 0
    last_fission_counter = None
    # last_counter_30mv = 0
    # last_counter_40 = 0
    # last_cur_integral = 0
    last_dt = None
    # flux1h = 0
    beam_off_time = 0
    first_curr_integral = None
    # first_counter_30mv = None
    first_fission_counter = None

    # for l in inFile:
    for line in file_lines:
        # Parse the line
        # line = l.split()  # ';')
        # the date is organized in this order:
        # Date;time;decimal of second; Diamond counter threshold = 40mV(counts);
        # Diamond counter th = 20mV(counts); Diamond counter th = 30mV(counts);
        # Fission Counter(counts); Integral Current uAh; Current uA
        year_date = line[0]
        day_time = line[1]
        sec_frac = line[2]
        # counter_30mv = float(line[5])
        fission_counter = float(line[6])
        # curr_integral = float(line[5])

        # Generate datetime for line
        # cur_dt = get_dt(year_date, day_time, sec_frac)
        cur_dt = datetime.strptime(year_date + " " + day_time + sec_frac, "%d/%m/%Y %H:%M:%S.%f")
        if start_dt <= cur_dt and first_fission_counter is None:
            first_fission_counter = fission_counter
            # first_counter_30mv = counter_30mv
            # last_counter_30mv = counter_30mv
            last_dt = cur_dt
            continue

        # if first_curr_integral is not None:
        if first_fission_counter is not None:
            if fission_counter == last_fission_counter:
                beam_off_time += (
                        cur_dt - last_dt).total_seconds()  # add the diff of beam off i and i - 1

            last_fission_counter = fission_counter
            last_dt = cur_dt

        if cur_dt > end_dt:
            interval_total_seconds = float((end_dt - start_dt).total_seconds())
            flux1h = ((last_fission_counter - first_fission_counter) * factor) / interval_total_seconds
            if flux1h < 0:
                raise ValueError(f"SOMETHING HAPPENED HERE {start_dt} {end_dt}")
            flux1h *= distance_factor
            return flux1h, beam_off_time
        elif first_curr_integral is not None:
            last_fission_counter = fission_counter
    return 0, 0


def check_distance_factor(distance_data, start_dt, board):
    for t in distance_data:
        if t['board'] in board and t['start'] <= start_dt <= t['end']:
            return float(t['Distance attenuation'])

    raise ValueError("Not suposed to be here {} {}".format(start_dt, board))


def get_distance_data(distance_factor_file):
    distance_data = []
    with open(distance_factor_file, "r") as csv_distance:
        dict_data = csv.DictReader(csv_distance, delimiter=',')
        for i in dict_data:
            to_append = i
            to_append['start'] = datetime.strptime(i['start'], "%m/%d/%Y").replace(hour=23, minute=59)
            to_append['end'] = datetime.strptime(i['end'], "%m/%d/%Y").replace(hour=23, minute=59)
            distance_data.append(to_append)
    return distance_data


def pre_process_data(full_file_lines):
    """
    I group all the possible configurations in a dictionary
    the key is
    MACHINE + BENCHMARK + LOG HEADER
    :param full_file_lines:
    :return: dictionary that contains the grouped benchmarks
    """
    grouped_benchmarks = {}
    for i in full_file_lines:
        # MACHINE + BENCHMARK + LOG HEADER
        key = i[1] + i[2] + i[3]
        if key not in grouped_benchmarks:
            grouped_benchmarks[key] = []

        grouped_benchmarks[key].append(i)

    return grouped_benchmarks


def main():
    if len(sys.argv) < 4:
        print("Usage: %s <neutron counts input file> <csv file> <factor> <distance factor file>" % (sys.argv[0]))
        sys.exit(1)

    in_file_name = sys.argv[1]
    csv_file_name = sys.argv[2]
    factor = float(sys.argv[3])
    distance_factor_file = sys.argv[4]
    # Load all distances before continue
    distance_data = get_distance_data(distance_factor_file=distance_factor_file)

    csv_out_file_summary = csv_file_name.replace(".csv", "_cross_section_summary.csv")
    print("in: " + csv_file_name)
    print("out: " + csv_out_file_summary)

    with open(csv_file_name, "r") as csv_input, open(csv_out_file_summary, "w") as csv_summary:
        # reading and starting the csvs files
        reader = csv.reader(csv_input, delimiter=';')
        writer_csv_summary = csv.writer(csv_summary, delimiter=';')
        header_summary = ["start timestamp", "end timestamp", "Machine", "benchmark",
                          "header info", "#lines computed", "#SDC", "#AccTime", "#(Abort==0)",
                          "Flux 1h", "Flux AccTime", "Fluence(Flux * $AccTime)",
                          "Fluence AccTime(FluxAccTime * $AccTime)", "Cross Section SDC",
                          "Cross Section Crash", "Time Beam Off (sec)", "Cross Section SDC AccTime",
                          "Cross Section Crash AccTime", "Time Beam Off AccTime (sec)", "distance_factor"]

        writer_csv_summary.writerow(header_summary)
        full_lines = list(reader)

        # grouping the benchmarks
        grouped_benchmarks = pre_process_data(full_file_lines=full_lines)

        # We need to read the neutron count files before calling get_fluency_flux
        file_lines = read_count_file(in_file_name)

        for bench in grouped_benchmarks:
            print("Parsing for {}".format(bench))
            lines = grouped_benchmarks[bench]
            i = 0
            while i < len(lines) - 1:
                try:
                    start_dt = datetime.strptime(lines[i][0].strip(), "%c")
                    end_dt = datetime.strptime(lines[i + 1][0].strip(), "%c")
                except ValueError as err:
                    print(err)
                    continue

                machine = lines[i][1]
                bench = lines[i][2]
                header_info = lines[i][3]
                # acc_time
                acc_time_s = float(lines[i][6])
                # #SDC
                sdc_s = int(lines[i][4])
                # abort
                abort_zero_s = 0
                if int(lines[i][7]) == 0 and int(lines[i][8]) == 0:
                    abort_zero_s += 1

                first_i = i
                while (end_dt - start_dt) < timedelta(minutes=ONE_HOUR):
                    i += 1
                    if i >= (len(lines) - 1):  # end of lines
                        break
                    if lines[first_i][2] != lines[i][2] or lines[first_i][3] != lines[i][3]:
                        print("ISSO ACONTECEU NATURALMENTE")
                        continue

                    acc_time_s += float(lines[i][6])
                    sdc_s += int(lines[i][4])
                    if int(lines[i][7]) == 0 and int(lines[i][8]) == 0:
                        abort_zero_s += 1

                    end_dt = datetime.strptime(lines[i + 1][0].strip(), "%c")

                # why consider such a small run???
                if (end_dt - start_dt) < SMALLEST_RUN:
                    i += 1
                    continue

                print("Generating cross section for {}, start {} end {}".format(bench.strip(), start_dt, end_dt))

                # compute 1h flux; sum SDC, ACC_TIME, Abort with 0; compute fluency (flux*(sum ACC_TIME))
                distance_factor = check_distance_factor(distance_data=distance_data, start_dt=start_dt,
                                                        board=lines[i][1])

                flux, time_beam_off = get_fluency_flux(start_dt=start_dt, end_dt=(start_dt + timedelta(minutes=60)),
                                                       file_lines=file_lines, factor=factor,
                                                       distance_factor=distance_factor)
                flux_acc_time, time_beam_off_acc_time = get_fluency_flux(start_dt=start_dt,
                                                                         end_dt=(
                                                                                 start_dt + timedelta(
                                                                             seconds=acc_time_s)),
                                                                         file_lines=file_lines, factor=factor,
                                                                         distance_factor=distance_factor)

                fluency = flux * acc_time_s
                fluency_acc_time = flux_acc_time * acc_time_s
                if fluency > 0:
                    cross_section = sdc_s / fluency
                    cross_section_crash = abort_zero_s / fluency
                else:
                    cross_section = 0
                    cross_section_crash = 0
                if fluency_acc_time > 0:
                    cross_section_acc_time = sdc_s / fluency_acc_time
                    cross_section_crash_acc_time = abort_zero_s / fluency_acc_time
                else:
                    cross_section_acc_time = 0
                    cross_section_crash_acc_time = 0

                # final row for full file, and almost final for summary file
                row = [start_dt.ctime(), end_dt.ctime(), machine, bench, header_info,
                       (i - first_i + 1), sdc_s, acc_time_s, abort_zero_s, flux,
                       flux_acc_time, fluency, fluency_acc_time,
                       cross_section, cross_section_crash, time_beam_off, cross_section_acc_time,
                       cross_section_crash_acc_time, time_beam_off_acc_time, distance_factor]

                writer_csv_summary.writerow(row)
                i += 1


#########################################################
#                    Main Thread                        #
#########################################################
if __name__ == '__main__':
    main()
