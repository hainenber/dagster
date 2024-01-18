import * as React from 'react';

import {ButtonGroup, ButtonGroupItem} from '@dagster-io/ui-components';

import {GanttChartMode} from './Constants';

export const GanttChartModeControl = React.memo(
  ({
    value,
    onChange,
    hideTimedMode,
  }: {
    value: GanttChartMode;
    hideTimedMode: boolean;
    onChange: (mode: GanttChartMode) => void;
  }) => {
    const buttons: ButtonGroupItem<GanttChartMode>[] = [
      {id: GanttChartMode.FLAT, icon: 'gantt_flat', tooltip: 'Flat view'},
      {id: GanttChartMode.WATERFALL, icon: 'gantt_waterfall', tooltip: 'Waterfall view'},
    ];

    if (!hideTimedMode) {
      buttons.push({
        id: GanttChartMode.WATERFALL_TIMED,
        icon: 'timer',
        tooltip: 'Timed view',
      });
    }

    const activeItems = React.useMemo(() => new Set([value]), [value]);
    const onClick = React.useCallback((id: GanttChartMode) => onChange(id), [onChange]);

    return <ButtonGroup activeItems={activeItems} buttons={buttons} onClick={onClick} />;
  },
);
