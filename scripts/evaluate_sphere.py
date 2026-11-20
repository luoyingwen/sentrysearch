"""Run four Sphere releases in disposable per-release index directories."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile


DATASETS = (
    ('sphere-benchmark-20261028-r02', 'sphere-cards-20261028-r01-proxy-media-47-old.zip'),
    ('sphere-public-video-jonghwa-12v-32q-20261104-r01', 'SP_eval_set_provided_by_Jonghwa.zip'),
    ('sphere-public-tsv360-cross-video-agreement-full-queries-20261116-r01', 'videos_fix_format_74_cross_video_agreement.zip'),
    ('sphere-public-qvhighlights-test-s50-20261113-r01', 'qvhighlights-test-s50-20261113-r01.zip'),
)


class AlignmentError(ValueError):
    pass


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def prepare(release, archive, work):
    queries = load(release / 'benchmark.json')['queries']
    videos = work / 'videos'
    videos.mkdir()
    with ZipFile(archive) as z:
        manifest = release / 'media.json'
        media = (load(manifest) if manifest.exists() else
                 json.loads(z.read('media-manifest.json'))['files'])
        assets = {m['asset_id'] for m in media}
        missing = {h['asset_id'] for q in queries for h in q['expected_hits']} - assets
        if missing:
            raise AlignmentError(f'{len(missing)} annotated assets missing: {", ".join(sorted(missing))}')
        names = {}
        for entry in z.infolist():
            names.setdefault(Path(entry.filename).name, []).append(entry)
        mapping = {}
        for i, m in enumerate(media):
            name = Path(m.get('archive_path') or m['path']).name
            matches = names.get(name, [])
            if not matches and release.name.startswith('sphere-public-tsv360-'):
                matches = names.get(Path(name).stem + '_LL360.mp4', [])
            if len(matches) != 1 or matches[0].file_size != m['size']:
                raise AlignmentError(f'{name}: expected size {m["size"]}; archive matches {[(e.filename, e.file_size) for e in matches]}')
            dest = videos / f'{i:04d}.mp4'
            with z.open(matches[0]) as src, dest.open('wb') as out:
                shutil.copyfileobj(src, out)
            with dest.open('rb') as src:
                if hashlib.file_digest(src, 'sha256').hexdigest() != m['sha256']:
                    raise AlignmentError(f'SHA-256 mismatch: {name}')
            mapping[str(dest)] = m['asset_id']
    (work / 'input.json').write_text(json.dumps([queries, mapping]), encoding='utf-8')
    return len(queries), len(mapping)


def worker(work, backend, model, eval_root, chunk_duration, overlap, dedupe, skip_still):
    sys.path.insert(0, str(eval_root))
    from sphere_eval.eval.metrics import EVAL_K, score_query, summarize
    from sentrysearch import cli, dlq, store
    from sentrysearch.chunker import _get_video_duration, expected_chunk_spans
    from sentrysearch.search import search_footage

    store.DEFAULT_DB_PATH = work / 'db'
    dlq.DEFAULT_DLQ_PATH = work / 'dlq.json'
    queries, mapping = load(work / 'input.json')
    durations = {name: _get_video_duration(name) for name in mapping}
    by_asset = {asset: durations[name] for name, asset in mapping.items()}
    for q in queries:
        for h in q['expected_hits']:
            if not 0 <= h['t_start'] <= h['t_end'] <= by_asset[h['asset_id']] + 0.5:
                (work / 'skip.txt').write_text(f'Annotation outside video: {q["query_id"]}: {h}', encoding='utf-8')
                return
    options = [str(work / 'videos'), '--backend', backend, '--chunk-duration', str(chunk_duration), '--overlap', str(overlap), '--target-resolution', '480', '--target-fps', '5', '--skip-still' if skip_still else '--no-skip-still']
    if backend == 'local':
        options += ['--model', model]
    cli.index.main(options, standalone_mode=False)
    db = store.SentryStore(db_path=work / 'db', backend=backend, model=model if backend == 'local' else None)
    from sentrysearch.embedder import get_embedder
    get_embedder(backend, model=model)
    if db._collection.count() == 0:
        raise RuntimeError('Empty index')
    if dlq.DEFAULT_DLQ_PATH.exists() and load(dlq.DEFAULT_DLQ_PATH):
        raise RuntimeError('Index contains embedding failures; see DLQ/log')
    for name, duration in durations.items():
        for start, _ in expected_chunk_spans(duration, chunk_duration=chunk_duration, overlap=overlap):
            if not skip_still and not db._collection.get(ids=[db.make_chunk_id(name, start)])['ids']:
                raise RuntimeError(f'Incomplete index: {mapping[name]} at {start}s')
    rows = []
    for q in queries:
        hits = search_footage(q['request']['query'], db, EVAL_K, dedupe_threshold=dedupe)
        adapted = [{'asset_id': mapping[h['source_file']], 't_start': h['start_time'],
                    't_end': h['end_time'], 'score': h['similarity_score']} for h in hits]
        rows.append({**q, **score_query(q, adapted)})
    (work / 'result.json').write_text(json.dumps(summarize(rows)), encoding='utf-8')
    (work / 'predictions.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    (work / 'index.json').write_text(json.dumps({'chunks': db._collection.count(), 'chunk_duration': chunk_duration, 'overlap': overlap, 'dedupe': dedupe, 'skip_still': skip_still}), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--releases', type=Path, default=Path.home() / 'work/lab/Sphere/sphere-eval/datasets/releases')
    parser.add_argument('--archives', type=Path, default=Path('D:/work/Sphere/benchmark_videos'))
    parser.add_argument('--output', type=Path, default=Path('sphere-evaluation.md'))
    parser.add_argument('--backend', choices=['local', 'gemini'], default='local')
    parser.add_argument('--model', default='qwen2b')
    parser.add_argument('--eval-root', type=Path, default=Path.home() / 'work/lab/Sphere/sphere-eval')
    parser.add_argument('--chunk-duration', type=int, default=6)
    parser.add_argument('--overlap', type=int, default=2)
    parser.add_argument('--dedupe', type=float, default=None)
    parser.add_argument('--skip-still', action='store_true')
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.chunk_duration <= 0 or not 0 <= args.overlap < args.chunk_duration:
        parser.error('Require 0 <= overlap < chunk-duration')
    if args.dedupe is not None and not 0 < args.dedupe <= 1:
        parser.error('Require 0 < dedupe <= 1')
    if args.worker:
        worker(args.worker, args.backend, args.model, args.eval_root, args.chunk_duration, args.overlap, args.dedupe, args.skip_still)
        return 0
    command = f'uv run --extra local python scripts/evaluate_sphere.py --chunk-duration {args.chunk_duration} --overlap {args.overlap}'
    if args.dedupe is not None:
        command += f' --dedupe {args.dedupe}'
    if args.skip_still:
        command += ' --skip-still'
    command += f' --output {args.output}'
    report = ['# Sphere 精度评估', '', f'后端：{args.backend}；模型：{args.model if args.backend == "local" else "gemini-embedding-2"}；768 维嵌入；{args.chunk_duration} 秒片段、{args.overlap} 秒重叠；全库检索；静态片段跳过：{args.skip_still}；检索去重阈值：{args.dedupe}；480p / 5 FPS 预处理；无重排。', '',
              '## Ranking and temporal overlap', '',
              '直接复用 sphere-eval/sphere_eval/eval/metrics.py：K=10，moment 命中要求同资产且 tIoU ≥ 0.5，asset 命中按同资产；候选按 tIoU 降序做一对一匹配。Recall@K 分母为 min(K, 标注数)；nDCG 使用二元命中；mean tIoU 仅统计 moment 查询，未命中标注计 0。no_result 查询不计入排名指标，另报假阳性率。', '',
              '每个数据集在独立临时目录运行，退出后删除视频、索引及失败队列，再开始下一个数据集。保留报告、日志与预测结果用于审计。TSV360 的 xxx.mp4 映射为 xxx_LL360.mp4，并继续严格验证文件大小和 SHA-256。', '',
              f'复现：`{command}`', '',
              '| 数据集 | 视频 | 查询 | 排名查询 | Recall@1 | Recall@10 | nDCG@10 | mean tIoU |',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    details = []
    failed = False
    for release, archive in DATASETS:
        print(release, flush=True)
        details.append(f'## {release}')
        try:
            with tempfile.TemporaryDirectory(prefix='sphere-eval-', dir='C:/Users/luoyw/AppData/Local/Temp/opencode') as temp:
                work = Path(temp)
                count, videos = prepare(args.releases / release, args.archives / archive, work)
                details.append(f'- SHA-256 对齐：{videos} 个视频；{count} 条查询。')
                artifact = f'{args.output.stem}.{release}'
                log = args.output.with_name(f'{artifact}.log')
                with log.open('wb') as stream:
                    worker_args = [sys.executable, __file__, '--worker', temp, '--backend', args.backend, '--model', args.model, '--eval-root', str(args.eval_root), '--chunk-duration', str(args.chunk_duration), '--overlap', str(args.overlap)]
                    if args.dedupe is not None:
                        worker_args += ['--dedupe', str(args.dedupe)]
                    if args.skip_still:
                        worker_args += ['--skip-still']
                    result = subprocess.run(worker_args, stdout=stream, stderr=subprocess.STDOUT)
                if result.returncode:
                    raise RuntimeError(f'评估进程退出码 {result.returncode}，详情见 `{log.name}`')
                if (work / 'skip.txt').exists():
                    raise AlignmentError((work / 'skip.txt').read_text(encoding='utf-8'))
                stats = load(work / 'result.json')
                formatted = [f'{stats[k]:.2%}' if stats[k] is not None else 'N/A'
                             for k in ('recall_at_1', 'recall_at_k', 'ndcg_at_k')]
                tiou = f'{stats["mean_tiou"]:.4f}' if stats['mean_tiou'] is not None else 'N/A'
                report.append(f'| {release} | {videos} | {count} | {stats["n"]} | ' + ' | '.join(formatted) + f' | {tiou} |')
                nr = stats['no_result']
                fpr = f'{nr["false_positive_rate"]:.2%}' if nr['false_positive_rate'] is not None else 'N/A'
                details.append(f'- 无答案查询：{nr["n"]}；假阳性数：{nr["false_positives"]}；假阳性率：{fpr}。')
                details.append(f'- 索引切片：{load(work / "index.json")["chunks"]}；运行日志：`{log.name}`。')
                shutil.copyfile(work / 'predictions.json', args.output.with_name(f'{artifact}.predictions.json'))
                shutil.copyfile(work / 'result.json', args.output.with_name(f'{artifact}.metrics.json'))
                shutil.copyfile(work / 'index.json', args.output.with_name(f'{artifact}.index.json'))
        except AlignmentError as exc:
            details.append(f'- 跳过：{exc}')
        except Exception as exc:
            failed = True
            details.append(f'- 未完成：{exc}')
        details.append('')
        args.output.write_text('\n'.join(report + ['', *details]), encoding='utf-8')
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
