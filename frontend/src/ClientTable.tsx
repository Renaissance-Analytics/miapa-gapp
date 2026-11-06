import { useEffect, useRef, useState } from 'react';
import { Button, Callout, Table } from '@particle-academy/react-fancy';
import { readClientCatalog, type CatalogSnapshot } from './client-catalog.js';

export type ClientColumn = 'name' | 'entity_count';
const labels: Record<ClientColumn, string> = { name: 'Client', entity_count: 'Entities' };

/** Only the live scoped service supplies rows; the saved schema supplies columns. */
export function ClientTable({ userId, columns }: { userId: string; columns: ClientColumn[] }) {
  const [state, setState] = useState<CatalogSnapshot>();
  const [phase, setPhase] = useState<'loading' | 'ready' | 'unavailable' | 'denied'>('loading');
  const generation = useRef(0);
  async function load() {
    const attempt = ++generation.current;
    setState(undefined); setPhase('loading');
    const result = await readClientCatalog(userId);
    if (attempt !== generation.current) return;
    if (result.kind === 'ok') { setState(result.value); setPhase('ready'); }
    else setPhase(result.kind === 'denied' ? 'denied' : 'unavailable');
  }
  useEffect(() => { void load(); return () => { generation.current++; }; }, [userId]);
  const current = state?.actor_id === userId ? state : undefined;
  return <section className="client-table" aria-label="Current client records" aria-busy={phase === 'loading'}>
    <div className="client-table-tools"><small>Current client records</small>
      <Button variant="ghost" size="sm" disabled={phase === 'loading'} onClick={() => { void load(); }}>Reload client table</Button></div>
    {phase === 'loading' && <p role="status">Loading current clients…</p>}
    {['unavailable', 'denied'].includes(phase) && <Callout color="amber">{phase === 'denied'
      ? 'Your current account cannot read these client records.' : 'We could not confirm current clients. Reload the table.'}</Callout>}
    {current && phase === 'ready' && <Table aria-label="Current clients" data-catalog-revision={current.revision}>
      <Table.Head><Table.Row>{columns.map(column => <Table.Cell key={column} header scope="col">{labels[column]}</Table.Cell>)}</Table.Row></Table.Head>
      <Table.Body>{current.engagements.map(row => <Table.Row key={row.id} data-client-id={row.id}>
        {columns.map(column => <Table.Cell key={column} data-client-column={column}>{row[column]}</Table.Cell>)}
      </Table.Row>)}</Table.Body>
      {!current.engagements.length && <Table.Tray>No client records yet. Continue Account setup in chat.</Table.Tray>}
    </Table>}
  </section>;
}
