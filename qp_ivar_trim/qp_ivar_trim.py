# -----------------------------------------------------------------------------
# Copyright (c) 2020, Qiita development team.
#
# Distributed under the terms of the BSD 3-clause License License.
#
# The full license is in the file LICENSE, distributed with this software.
# -----------------------------------------------------------------------------
import pandas as pd
from os import environ
from os.path import basename, join
from glob import glob
from qiita_client import ArtifactInfo

MEMORY = '16g'
WALLTIME = '30:00:00'
FINISH_MEMORY = '10g'
FINISH_WALLTIME = '10:00:00'
MAX_RUNNING = 8
QC_REFERENCE = environ["QC_REFERENCE"]

IVAR_TRIM_BASE = 'ivar trim -x {primer_offset} -e -i %s -b {primer}'\
    ' -p {out_dir}/%s'
# IVAR_TRIM_CMD = 'gunzip %s ivar trim -x {nprocs} -e -b {primer} -i %s
# -p {out_dir}/%s; gzip {out_dir}/%s'
GZIP_CMD = 'gzip {out_dir}/%s'
GUNZIP_CMD = 'gunzip %s'
IVAR_TRIM_CMD = f'{GUNZIP_CMD}; {IVAR_TRIM_BASE}; {GZIP_CMD}'


def get_primer():
    folder = QC_REFERENCE

    # skip human database
    return [basename(f) for f in glob(f'{folder}/*.bed')][0]


def _generate_commands(untrimmed_bams_gz, primer, primer_offset, out_dir):
    """Helper function to generate commands and facilite testing"""
    cmd = IVAR_TRIM_CMD
    files = untrimmed_bams_gz
    command = cmd.format(primer_offset=primer_offset,
                         primer=primer, out_dir=out_dir)

    out_files = []
    commands = []
    for bam_gz in files:
        fname_gz = basename(bam_gz)
        fname = fname_gz[:-3]
        bam = bam_gz[:-3]
        out_files.append((f'{out_dir}/{fname_gz}', 'tgz'))

        cmd = command % (bam_gz, bam, fname, fname)
        commands.append(cmd)

    return commands, out_files


def ivar_trim(qclient, job_id, parameters, out_dir):
    """Run ivar trim with the given parameters

    Parameters
    ----------
    qclient : tgp.qiita_client.QiitaClient
        The Qiita server client
    job_id : str
        The job id
    parameters : dict
        The parameter values to run split libraries
    out_dir : str
        The path to the job's output directory

    Returns
    -------
    bool, list, str
        The results of the job
    """

    qclient.update_job_step(
        job_id, "Step 3 of 4: Finishing ivar trim")

    ainfo = []
    # Generates 2 artifacts: one for the ribosomal
    # reads and other for the non-ribosomal reads
    out_files = []
    with open(f'{out_dir}/{job_id}.out_files.tsv') as f:
        for line in f.readlines():
            fp, ft = line.split()
            out_files.append((fp, ft))

    # Step 4 generating artifacts
    msg = "Step 4 of 4: Generating new artifact"
    qclient.update_job_step(job_id, msg)
    ainfo = [ArtifactInfo('Trimmed files', 'BAM', out_files)]

    return True, ainfo, ""


def ivar_trim_to_array(files, out_dir, params, prep_info, url, job_id):
    """Creates qsub files for submission of per sample fastp and minimap2

    Parameters
    ----------
    files : dict
        The dictionary of files to process, raw_forward_seqs/raw_reverse_seqs
    out_dir : str
        The output directory
    params : dict
        The parameter values to run ivar trim
    prep_info : str
        The path to prep_info
    url : str
        The url to send info to
    job_id : str
        The job id

    Returns
    -------
    str, str, str
        The paths of the main_qsub_fp, finish_qsub_fp, out_files_fp
    """
    # primer = None
    # if params['primer'] != 'None':
    #     list = get_primer()
    #     print(params['primer'])
    #     primer = [join(QC_REFERENCE, f'{db}')
    #               for db in list]
    primer = join(QC_REFERENCE, get_primer())


#    if 'raw_reverse_seqs' in files:
#        rev_seqs = sorted(files['raw_reverse_seqs'])
#    else:
#        rev_seqs = []
    df = pd.read_csv(prep_info, sep='\t', dtype='str',
                     na_values=[], keep_default_na=True)
    df.set_index('sample_name', inplace=True)
    if 'run_prefix' not in df.columns:
        raise ValueError('Missing run_prefix column in your preparation')

    # Note that for processing we don't actually need the run_prefix so
    # we are not going to use it and simply loop over the ordered
    # fwd_seqs/rev_seqs
    commands, out_files = _generate_commands(
        files['tgz'], primer, params['primer_offset'], out_dir)

    # writing the job array details
    details_name = join(out_dir, 'ivar_trim.array-details')
    with open(details_name, 'w') as details:
        details.write('\n'.join(commands))
    n_jobs = len(commands)

    # all the setup pieces
    lines = ['#!/bin/bash',
             '#PBS -M qiita.help@gmail.com',
             f'#PBS -N {job_id}',
             '#PBS -l nodes=1',
             f'#PBS -l walltime={WALLTIME}',
             f'#PBS -l mem={MEMORY}',
             f'#PBS -o {out_dir}/{job_id}' + '_${PBS_ARRAYID}.log',
             f'#PBS -e {out_dir}/{job_id}' + '_${PBS_ARRAYID}.err',
             f'#PBS -t 1-{n_jobs}%{MAX_RUNNING}',
             '#PBS -l epilogue=/home/qiita/qiita-epilogue.sh',
             'set -e',
             f'cd {out_dir}',
             f'{params["environment"]}',
             'date',  # start time
             'hostname',  # executing system
             'echo ${PBS_JOBID} ${PBS_ARRAYID}',
             'offset=${PBS_ARRAYID}',
             'step=$(( $offset - 0 ))',
             f'cmd=$(head -n $step {details_name} | tail -n 1)',
             'eval $cmd',
             'set +e',
             'date']
    main_qsub_fp = join(out_dir, f'{job_id}.qsub')
    with open(main_qsub_fp, 'w') as job:
        job.write('\n'.join(lines))
        job.write('\n')

    # finish job
    lines = ['#!/bin/bash',
             '#PBS -M qiita.help@gmail.com',
             f'#PBS -N finish-{job_id}',
             '#PBS -l nodes=1',
             f'#PBS -l walltime={FINISH_WALLTIME}',
             f'#PBS -l mem={FINISH_MEMORY}',
             f'#PBS -o {out_dir}/finish-{job_id}.log',
             f'#PBS -e {out_dir}/finish-{job_id}.err',
             '#PBS -l epilogue=/home/qiita/qiita-epilogue.sh',
             'set -e',
             f'cd {out_dir}',
             f'{params["environment"]}',
             'date',  # start time
             'hostname',  # executing system
             'echo $PBS_JOBID',
             f'finish_qp_ivar_trim {url} {job_id} {out_dir}\n'
             "date"]
    finish_qsub_fp = join(out_dir, f'{job_id}.finish.qsub')
    with open(finish_qsub_fp, 'w') as out:
        out.write('\n'.join(lines))
        out.write('\n')

    out_files_fp = join(out_dir, f'{job_id}.out_files.tsv')
    with open(out_files_fp, 'w') as out:
        out.write('\n'.join([f'{fp}\t{ft}'for fp, ft in out_files]))

    return main_qsub_fp, finish_qsub_fp, out_files_fp
