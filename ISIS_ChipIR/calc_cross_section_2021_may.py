#!/usr/bin/env python3
import sys
import pandas as pd
import numpy as np
from datetime import datetime

# Time for each run
SECONDS_1h = 3600
TIME_SLOT_FOR_FLUENCY = pd.Timedelta(seconds=SECONDS_1h)


def read_count_file(in_file_name):
    """
    Read neutron log file
    :param in_file_name: neutron log filename
    :return: numpy array with all neutron lines
    """
    file_lines = list()
    with open(in_file_name, 'r') as in_file:
        for line in in_file:
            # Sanity check, we require a date at the beginning of the line
            line_split = line.rstrip().split()
            if len(line_split) < 7:
                print(f"Ignoring line (malformed):{line}")
                continue
            year_date, day_time, sec_frac = line_split[0], line_split[1], line_split[2]
            fission_counter = float(line_split[6])

            # Generate datetime for line
            cur_dt = datetime.strptime(year_date + " " + day_time + sec_frac, "%d/%m/%Y %H:%M:%S.%f")
            # It is faster to modify the lines on source
            file_lines.append(np.array([cur_dt, fission_counter]))
    return np.array(file_lines)


def get_fluency_flux(start_dt, end_dt, neutron_count, factor, distance_factor):
    """
    -- Fission counters are the ChipIR counters -- index 6 in the ChipIR log
    -- Current Integral are the synchrotron output -- index 7 in the ChipIR log
    """
    three_seconds = pd.Timedelta(seconds=3)
    # Slicing the neutron count to use only the useful information
    # It is efficient because it returns a view of neutron count
    neutron_count_cut = neutron_count[(neutron_count[:, 0] >= start_dt) &
                                      (neutron_count[:, 0] <= (end_dt + three_seconds))]
    flux, beam_off_time, last_fission_counter = 0, 0, None
    # Get the first from the list
    last_dt, first_fission_counter = neutron_count_cut[0]
    # Loop thought the neutron to find the beam off
    for (cur_dt, fission_counter) in neutron_count_cut[1:]:
        if fission_counter == last_fission_counter:
            beam_off_time += (cur_dt - last_dt).total_seconds()
        last_fission_counter = fission_counter
        last_dt = cur_dt

    interval_total_seconds = float((end_dt - start_dt).total_seconds())
    # flux1h = ((last_fission_counter - first_fission_counter) * factor) / interval_total_seconds
    flux = (factor * (1 - (beam_off_time / interval_total_seconds)))
    error_str = f"FLUX<0 {start_dt} {end_dt} {flux} {last_fission_counter} {interval_total_seconds} {beam_off_time}"
    assert flux >= 0, error_str

    flux *= distance_factor
    return flux, beam_off_time


def generate_cross_section(row, distance_data, neutron_count):
    start_dt = row["start_dt"]
    end_dt = row["end_dt"]
    machine = row["machine"]
    acc_time = row["acc_time"]
    sdc_s = row["#SDC"]
    due_s = row["#DUE"]
    distance_line = distance_data[(distance_data["board"].str.contains(machine)) &
                                  (distance_data["start"] <= start_dt) & (start_dt <= distance_data["end"])]
    # factor = float(distance_line["factor"])
    # TODO: GAMBIARRA
    factor = 5.6e6

    distance_factor = float(distance_line["Distance attenuation"])

    print(f"Generating cross section for {row['benchmark']}, start {start_dt} end {end_dt}")
    flux, time_beam_off = get_fluency_flux(start_dt=start_dt, end_dt=end_dt, neutron_count=neutron_count,
                                           factor=factor, distance_factor=distance_factor)
    fluency = flux * acc_time
    cross_section_sdc = cross_section_due = 0
    if fluency > 0:
        cross_section_sdc = sdc_s / fluency
        cross_section_due = due_s / fluency

    row["end_dt"] = end_dt
    row["Flux 1h"] = flux
    row["Fluency(Flux * $AccTime)"] = fluency
    row["Cross Section SDC"] = cross_section_sdc
    row["Cross Section DUE"] = cross_section_due
    row["Time Beam Off"] = time_beam_off
    return row


def get_end_times(group):
    # Check if it is sorted
    assert group.equals(group.sort_values())
    end_dt_group = group.copy()
    # Find the time slots
    first = group.iloc[0]
    for i in range(len(group)):
        if (group.iloc[i] - first) > TIME_SLOT_FOR_FLUENCY:
            first = group.iloc[i]
        end_dt_group.iloc[i] = first + TIME_SLOT_FOR_FLUENCY
    return end_dt_group


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <neutron counts input file> <csv file> <distance factor file>")
        exit(1)

    neutron_count_file = sys.argv[1]
    csv_file_name = sys.argv[2]
    distance_factor_file = sys.argv[3]
    print(f"Generating cross section for {csv_file_name}")
    print(f"- {distance_factor_file} for distance")
    print(f"- {neutron_count_file} for neutrons")

    # Load all distances before continue
    distance_data = pd.read_csv(distance_factor_file)
    # Replace the hours and the minutes to the last
    distance_data["start"] = distance_data["start"].apply(
        lambda row: datetime.strptime(row, "%m/%d/%Y").replace(hour=0, minute=0)
    )
    distance_data["end"] = distance_data["end"].apply(
        lambda row: datetime.strptime(row, "%m/%d/%Y").replace(hour=23, minute=59)
    )
    # -----------------------------------------------------------------------------------------------------------------
    # We need to read the neutron count files before calling get_fluency_flux
    neutron_count = read_count_file(neutron_count_file)

    csv_out_file_summary = csv_file_name.replace(".csv", "_cross_section.csv")
    print(f"in: {csv_file_name}")
    print(f"out: {csv_out_file_summary}")
    # -----------------------------------------------------------------------------------------------------------------
    # Read the input csv file
    input_df = pd.read_csv(csv_file_name, delimiter=';').drop("file_path", axis="columns")

    # Before continue we need to invert the logic of abort and end
    input_df["#DUE"] = input_df.apply(lambda row: 1 if row["#end"] == 0 and row["#abort"] == 0 else 0, axis="columns")
    # Convert time to datetime
    input_df["time"] = pd.to_datetime(input_df["time"])
    # Rename the column
    input_df = input_df.rename(columns={"time": "start_dt"}).sort_values(by=["start_dt"])

    # Separate the runs that are bigger than 1h
    runs_bigger_than_1h = input_df[input_df["acc_time"] > SECONDS_1h].copy()
    runs_bigger_than_1h["end_dt"] = runs_bigger_than_1h["start_dt"] + pd.to_timedelta(runs_bigger_than_1h["acc_time"],
                                                                                      unit='s')
    runs_bigger_than_1h = runs_bigger_than_1h.groupby(['machine', 'benchmark', 'header', 'start_dt', 'end_dt']).sum()

    # Group by hours. Only 1h runs can be grouped
    runs_1h = input_df[input_df["acc_time"] <= SECONDS_1h].copy()
    runs_1h['end_dt'] = runs_1h.groupby(['machine', 'benchmark', 'header'])['start_dt'].transform(get_end_times)
    runs_1h = runs_1h.groupby(['machine', 'benchmark', 'header', 'end_dt']).agg(
        {'start_dt': 'first', '#SDC': 'sum',
         '#abort': 'sum', '#end': 'sum', 'acc_time': 'sum',
         'acc_err': 'sum', '#DUE': 'sum'
         }).reset_index().set_index(['machine', 'benchmark', 'header', 'start_dt', 'end_dt'])

    # Create a final df
    final_df = pd.concat([runs_1h, runs_bigger_than_1h]).reset_index()

    # Apply generate_cross section function
    final_df = final_df.apply(generate_cross_section, axis="columns", args=(distance_data, neutron_count))
    # Reorder before saving
    final_df = final_df[['start_dt', 'end_dt', 'machine', 'benchmark', 'header', '#SDC', '#DUE', '#abort', '#end',
                         'acc_time', 'Time Beam Off', 'acc_err', 'Flux 1h', 'Fluency(Flux * $AccTime)',
                         'Cross Section SDC', 'Cross Section DUE']]
    print(final_df)
    final_df.to_csv(csv_out_file_summary, index=False, date_format="%Y-%m-%d %H:%M:%S")


#########################################################
#                    Main Thread                        #
#########################################################
if __name__ == '__main__':
    main()
