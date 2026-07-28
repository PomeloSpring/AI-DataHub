import { memo, useRef, useEffect, useState } from 'react';
import { Chart } from '@antv/g2';
import * as topojson from 'topojson-client';
import {
  BarChart3, TrendingUp, PieChart, Activity, Circle, Webhook, Filter, ArrowDownUp,
  Type, Table, Hash, Gauge, LineChart, BarChart, AreaChart, Calendar,
  Grid3x3, Box, Aperture, ArrowRightLeft, GitBranch, LayoutGrid,
  Flower2, Target, Cloud, Map, List, ListChecks, Sliders,
  Search, RotateCcw, Download,
} from 'lucide-react';
import { useThemeStore } from '../stores/themeStore';
import { Label } from '@/components/ui/label';
import { useDashboardStore } from '../stores/dashboardStore';
import ModalPage from './ModalPage';

// Lucide icon map for chart types
const CHART_ICON_MAP: Record<string, React.ComponentType<any>> = {
  BarChart3, TrendingUp, PieChart, Activity, Circle, Webhook, Filter, ArrowDownUp,
  Type, Table, Hash, Gauge, LineChart, BarChart, AreaChart, Calendar,
  Grid3x3, Box, Aperture, ArrowRightLeft, GitBranch, LayoutGrid,
  Flower2, Target, Cloud, Map, List, ListChecks, Sliders,
  Search, RotateCcw, Download,
};

export function ChartIcon({ name, className }: { name: string; className?: string }) {
  const Icon = CHART_ICON_MAP[name];
  if (!Icon) return <Circle className={className} />;
  return <Icon className={className} />;
}

interface Props {
  chartType: string;
  data: { columns: string[]; rows: any[]; total?: number };
  config?: Record<string, any>;
  style?: React.CSSProperties;
  loading?: boolean;
  chartId?: number;
}

// Map data URLs
const MAP_URLS = {
  china: 'https://assets.antv.antgroup.com/g2/china-topo.json',
  world: 'https://assets.antv.antgroup.com/g2/world-topo.json',
};

// Color palettes
const LIGHT_COLORS = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4', '#ea7ccc'];
const DARK_COLORS = ['#4992ff', '#7cffb2', '#fddd60', '#ff6e76', '#58d9f9', '#05c091', '#ff8a45', '#8d48e3', '#dd79ff'];

// Helper: check if data has group column for multi-series
export function detectGroupColumn(columns: string[], rows: any[], config: Record<string, any>): string | null {
  if (config?.groupCol) return config.groupCol;

  const xCol = config?.xCol || columns.find(c => typeof rows[0]?.[c] === 'string') || columns[0];
  const yCol = config?.yCol || columns.find(c => typeof rows[0]?.[c] === 'number') || columns[1];

  const candidateCols = columns.filter(c => {
    if (c === xCol || c === yCol) return false;
    if (typeof rows[0]?.[c] !== 'string') return false;
    const uniqueVals = new Set(rows.map(r => r[c]));
    return uniqueVals.size > 1 && uniqueVals.size < rows.length;
  });

  return candidateCols[0] || null;
}

// Helper: detect if a column contains time/date values
export function isTimeColumn(values: any[]): boolean {
  if (!values.length) return false;
  const sample = values.slice(0, 10);
  const timePatterns = [
    /^\d{4}-\d{2}-\d{2}/,
    /^\d{4}\/\d{2}\/\d{2}/,
    /^\d{4}\d{2}\d{2}/,
    /^\d{2}:\d{2}:\d{2}/,
    /^\d{10,13}$/,
  ];
  return sample.every(v => {
    const str = String(v);
    return timePatterns.some(p => p.test(str)) || !isNaN(Date.parse(str));
  });
}

// Helper: parse time value to Date object
export function parseTime(value: any): Date | null {
  if (!value) return null;
  const str = String(value);
  if (/^\d{10}$/.test(str)) return new Date(Number(str) * 1000);
  if (/^\d{13}$/.test(str)) return new Date(Number(str));
  const date = new Date(str);
  return isNaN(date.getTime()) ? null : date;
}

// Helper: aggregate time series data
export function aggregateTimeData(
  rows: any[], xCol: string, yCol: string, groupCol: string | null,
  aggMethod: string = 'auto', timeGranularity: string = 'auto'
) {
  const parsedRows = rows.map(r => ({
    ...r,
    _time: parseTime(r[xCol]),
    _value: Number(r[yCol]) || 0,
  })).filter(r => r._time !== null);

  parsedRows.sort((a, b) => a._time!.getTime() - b._time!.getTime());

  let granularity = timeGranularity;
  if (granularity === 'auto' && parsedRows.length >= 2) {
    const first = parsedRows[0]._time!;
    const last = parsedRows[parsedRows.length - 1]._time!;
    const diffMs = last.getTime() - first.getTime();
    const diffDays = diffMs / (1000 * 60 * 60 * 24);

    if (diffDays <= 2) granularity = 'hour';
    else if (diffDays <= 90) granularity = 'day';
    else if (diffDays <= 730) granularity = 'week';
    else if (diffDays <= 1825) granularity = 'month';
    else granularity = 'year';
  }

  const formatTime = (date: Date): string => {
    switch (granularity) {
      case 'hour':
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:00`;
      case 'day':
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
      case 'week': {
        const startOfWeek = new Date(date);
        startOfWeek.setDate(date.getDate() - date.getDay());
        return `${startOfWeek.getFullYear()}-W${String(Math.ceil((startOfWeek.getDate()) / 7)).padStart(2, '0')}`;
      }
      case 'month':
        return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
      case 'year':
        return `${date.getFullYear()}`;
      default:
        return date.toISOString().slice(0, 10);
    }
  };

  const aggFunc = (values: number[], method: string): number => {
    switch (method) {
      case 'sum': return values.reduce((a, b) => a + b, 0);
      case 'avg': return values.reduce((a, b) => a + b, 0) / values.length;
      case 'max': return Math.max(...values);
      case 'min': return Math.min(...values);
      case 'count': return values.length;
      default: return values.reduce((a, b) => a + b, 0);
    }
  };

  if (groupCol) {
    const groups: Record<string, Record<string, number[]>> = {};
    parsedRows.forEach(r => {
      const timeKey = formatTime(r._time!);
      const groupVal = String(r[groupCol!]);
      if (!groups[groupVal]) groups[groupVal] = {};
      if (!groups[groupVal][timeKey]) groups[groupVal][timeKey] = [];
      groups[groupVal][timeKey].push(r._value);
    });

    const allTimeKeysSet: Record<string, boolean> = {};
    parsedRows.forEach(r => { allTimeKeysSet[formatTime(r._time!)] = true; });
    const allTimeKeys = Object.keys(allTimeKeysSet).sort();
    const seriesData: Record<string, (number | null)[]> = {};
    Object.entries(groups).forEach(([name, timeMap]) => {
      seriesData[name] = allTimeKeys.map(key => {
        const vals = timeMap[key];
        return vals ? aggFunc(vals, aggMethod) : null;
      });
    });

    return { xData: allTimeKeys, seriesData };
  } else {
    const timeMap: Record<string, number[]> = {};
    parsedRows.forEach(r => {
      const timeKey = formatTime(r._time!);
      if (!timeMap[timeKey]) timeMap[timeKey] = [];
      timeMap[timeKey].push(r._value);
    });

    const xData = Object.keys(timeMap).sort();
    const yData = xData.map(key => aggFunc(timeMap[key], aggMethod));
    return { xData, yData };
  }
}

// Helper: group data by a column
export function groupDataByColumn(rows: any[], xCol: string, yCol: string, groupCol: string) {
  const xSet: Record<string, boolean> = {};
  const groups: Record<string, Record<string, number>> = {};

  rows.forEach(r => {
    const xVal = String(r[xCol]);
    const groupVal = String(r[groupCol]);
    const yVal = Number(r[yCol]) || 0;

    xSet[xVal] = true;
    if (!groups[groupVal]) groups[groupVal] = {};
    groups[groupVal][xVal] = yVal;
  });

  let xData = Object.keys(xSet);
  const isTime = isTimeColumn(xData);

  if (isTime) {
    xData.sort((a, b) => {
      const da = parseTime(a);
      const db = parseTime(b);
      if (da && db) return da.getTime() - db.getTime();
      return a.localeCompare(b);
    });
  } else {
    xData.sort();
  }

  const seriesData: Record<string, (number | null)[]> = {};
  Object.entries(groups).forEach(([name, valMap]) => {
    seriesData[name] = xData.map(x => x in valMap ? valMap[x] : null);
  });

  return { xData, seriesData };
}

// Build G2 spec based on chart type
export function buildG2Spec(chartType: string, columns: string[], rows: any[], config: Record<string, any>, isDark: boolean, width: number, height: number) {
  const colors = isDark ? DARK_COLORS : LIGHT_COLORS;
  const textColor = isDark ? '#f0f0f0' : '#333';
  const bgColor = isDark ? '#1f1f3a' : '#ffffff';
  // Axis label styling — brighter, larger, bolder on dark backgrounds
  const axisLabelStyle = isDark
    ? { labelFill: '#f0f0f0', labelFontSize: 12, labelFontWeight: '600' as const }
    : { labelFill: '#555', labelFontSize: 11, labelFontWeight: '400' as const };
  const legendLabelStyle = isDark
    ? { fill: '#f0f0f0', fontSize: 12, fontWeight: '600' as const }
    : { fill: '#555', fontSize: 11 };

  const xCol = config?.xCol || columns.find(c => typeof rows[0]?.[c] === 'string') || columns[0];
  const yCol = config?.yCol || columns.find(c => typeof rows[0]?.[c] === 'number') || columns[1];

  // Detect group column
  const groupCol = detectGroupColumn(columns, rows, config);
  const hasGroups = groupCol && ['line', 'bar', 'area'].includes(chartType);

  // Check if x-axis is time data
  const xValues = rows.map(r => r[xCol]);
  const isTimeData = isTimeColumn(xValues);

  // Time aggregation settings
  const enableTimeAgg = config?.enableTimeAgg === true;
  const timeGranularity = config?.timeGranularity || 'auto';
  const aggMethod = config?.aggMethod || 'sum';

  // Prepare data
  let processedData: any[] = [];
  let seriesField: string | null = null;

  if (enableTimeAgg && isTimeData) {
    const aggResult = aggregateTimeData(rows, xCol, yCol, groupCol, aggMethod, timeGranularity);
    if (groupCol && aggResult.seriesData) {
      // Multi-series with time aggregation
      seriesField = groupCol;
      Object.entries(aggResult.seriesData).forEach(([name, values]) => {
        aggResult.xData.forEach((x, i) => {
          if (values[i] !== null) {
            processedData.push({ [xCol]: x, [yCol]: values[i], [groupCol]: name });
          }
        });
      });
    } else {
      // Single series with time aggregation
      processedData = aggResult.xData.map((x, i) => ({
        [xCol]: x,
        [yCol]: aggResult.yData![i],
      }));
    }
  } else if (hasGroups) {
    // Multi-series without time aggregation
    seriesField = groupCol;
    const grouped = groupDataByColumn(rows, xCol, yCol, groupCol!);
    Object.entries(grouped.seriesData).forEach(([name, values]) => {
      grouped.xData.forEach((x, i) => {
        if (values[i] !== null) {
          processedData.push({ [xCol]: x, [yCol]: values[i], [groupCol!]: name });
        }
      });
    });
  } else {
    // Single series, sort by time if needed
    if (isTimeData) {
      const sortedRows = [...rows].sort((a, b) => {
        const da = parseTime(a[xCol]);
        const db = parseTime(b[xCol]);
        if (da && db) return da.getTime() - db.getTime();
        return 0;
      });
      processedData = sortedRows.map(r => ({ ...r }));
    } else {
      processedData = rows.map(r => ({ ...r }));
    }
  }

  // Build spec based on chart type
  const baseSpec: any = {
    width,
    height,
    autoFit: false,
    theme: isDark ? 'classicDark' : 'classic',
    padding: [40, 24, 40, 24],
    style: {
      viewFill: bgColor,
    },
  };

  switch (chartType) {
    case 'bar':
      return {
        ...baseSpec,
        type: 'interval',
        data: processedData,
        encode: { x: xCol, y: yCol, color: seriesField || undefined },
        axis: {
          x: { labelAutoRotate: true, style: axisLabelStyle },
          y: { style: axisLabelStyle },
        },
        scale: { color: { range: colors } },
        style: { radiusTopLeft: 4, radiusTopRight: 4 },
        interaction: { tooltip: { shared: true } },
      };

    case 'line':
      return {
        ...baseSpec,
        type: 'line',
        data: processedData,
        encode: { x: xCol, y: yCol, color: seriesField || undefined, shape: 'smooth' },
        axis: {
          x: { labelAutoRotate: true, style: axisLabelStyle },
          y: { style: axisLabelStyle },
        },
        scale: { color: { range: colors } },
        style: { lineWidth: 2 },
        point: { shapeSize: 3 },
        interaction: { tooltip: { shared: true } },
      };

    case 'area':
      return {
        ...baseSpec,
        type: 'area',
        data: processedData,
        encode: { x: xCol, y: yCol, color: seriesField || undefined, shape: 'smooth' },
        axis: {
          x: { labelAutoRotate: true, style: axisLabelStyle },
          y: { style: axisLabelStyle },
        },
        scale: { color: { range: colors } },
        style: { fillOpacity: 0.3 },
        interaction: { tooltip: { shared: true } },
      };

    case 'pie': {
      const pieData = rows.map(r => ({
        name: String(r[xCol]),
        value: Number(r[yCol]) || 0,
      }));
      return {
        ...baseSpec,
        type: 'interval',
        data: pieData,
        encode: { x: 'name', y: 'value', color: 'name' },
        transform: [{ type: 'stackY' }],
        coordinate: { type: 'theta', innerRadius: 0.4 },
        scale: { color: { range: colors } },
        style: { stroke: bgColor, lineWidth: 2 },
        labels: [{ text: 'name', position: 'outside', style: { fontSize: 11, fill: textColor } }],
        legend: { color: { position: 'right', itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'scatter':
      return {
        ...baseSpec,
        type: 'point',
        data: processedData,
        encode: { x: xCol, y: yCol, color: seriesField || undefined },
        scale: { color: { range: colors } },
        style: { r: 4, opacity: 0.7 },
        axis: {
          x: { style: axisLabelStyle },
          y: { style: axisLabelStyle },
        },
        interaction: { tooltip: {} },
      };

    case 'radar': {
      const radarCols = columns.filter(c => typeof rows[0]?.[c] === 'number').slice(0, 8);
      const radarData: any[] = [];
      rows.slice(0, 5).forEach(r => {
        radarCols.forEach(col => {
          radarData.push({ name: col, value: Number(r[col]) || 0, category: String(r[xCol]) });
        });
      });
      return {
        ...baseSpec,
        type: 'line',
        data: radarData,
        encode: { x: 'name', y: 'value', color: 'category' },
        coordinate: { type: 'polar' },
        scale: { color: { range: colors } },
        style: { lineWidth: 2 },
        axis: { x: { style: axisLabelStyle }, y: { style: axisLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'gauge': {
      const val = Number(rows[0]?.[yCol]) || 0;
      return {
        ...baseSpec,
        type: 'gauge',
        data: { value: val },
        style: {
          pointerShape: 'pointer',
          pinShape: 'circle',
          labelTextFill: textColor,
          labelTextFontSize: 13,
          labelTextFontWeight: '600',
        },
        scale: { color: { range: colors } },
      };
    }

    case 'funnel': {
      const funnelData = rows.map(r => ({
        action: String(r[xCol]),
        pv: Number(r[yCol]) || 0,
      })).sort((a, b) => b.pv - a.pv);

      return {
        ...baseSpec,
        type: 'interval',
        data: funnelData,
        encode: { x: 'action', y: 'pv', color: 'action', shape: 'funnel' },
        transform: [{ type: 'symmetryY' }],
        scale: { x: { padding: 0 }, color: { range: colors } },
        coordinate: { transform: [{ type: 'transpose' }] },
        axis: false,
        style: { stroke: bgColor, lineWidth: 1 },
        labels: [{
          text: (d: any) => `${d.action}\n${d.pv}`,
          position: 'inside',
          transform: [{ type: 'contrastReverse' }],
          style: { fill: '#fff', fontSize: 11 }
        }],
        legend: { color: { position: 'right', itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'heatmap': {
      const valCols = columns.filter(c => typeof rows[0]?.[c] === 'number').slice(0, 10);
      const heatData: any[] = [];
      rows.forEach(r => {
        valCols.forEach(vc => {
          heatData.push({ x: String(r[xCol]), y: vc, value: Number(r[vc]) || 0 });
        });
      });
      return {
        ...baseSpec,
        type: 'cell',
        data: heatData,
        encode: { x: 'x', y: 'y', color: 'value' },
        scale: { color: { palette: isDark ? ['#14142a', '#177ddc', '#4992ff'] : ['#f0f5ff', '#5470c6', '#1a3a6e'] } },
        style: { inset: 1 },
        labels: [{ text: 'value', style: { fill: textColor, fontSize: 10 } }],
        axis: {
          x: { labelAutoRotate: true, style: axisLabelStyle },
          y: { style: axisLabelStyle },
        },
        legend: { color: { position: 'right', itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'text_display': {
      // Text display for count/KPI metrics with optional YoY/MoM comparison
      const value = rows.length > 0 ? Number(rows[0][yCol]) || 0 : 0;

      // Format value
      const formatValue = (val: number, fmt?: string) => {
        switch (fmt) {
          case 'percent': return `${val.toFixed(1)}%`;
          case 'currency': return `¥${val.toLocaleString()}`;
          case 'raw': return String(val);
          default: return val.toLocaleString();
        }
      };

      const prefix = config?.valuePrefix || '';
      const suffix = config?.valueSuffix || '';
      const valueFontSize = config?.valueFontSize || 48;
      const formattedValue = `${prefix}${formatValue(value, config?.valueFormat)}${suffix}`;

      // YoY (同比) comparison
      const yoyColumn = config?.yoyColumn;
      const yoyValue = yoyColumn && rows.length > 0 ? Number(rows[0][yoyColumn]) : null;
      const yoyChange = yoyValue && yoyValue !== 0 ? ((value - yoyValue) / yoyValue * 100) : null;

      // MoM (环比) comparison
      const momColumn = config?.momColumn;
      const momValue = momColumn && rows.length > 0 ? Number(rows[0][momColumn]) : null;
      const momChange = momValue && momValue !== 0 ? ((value - momValue) / momValue * 100) : null;

      const showComparison = config?.showComparison !== false;

      return {
        ...baseSpec,
        type: 'view',
        children: [
          // Main value
          {
            type: 'text',
            data: { value: null },
            style: {
              text: formattedValue,
              x: width / 2,
              y: height / 2 - (showComparison && (yoyChange !== null || momChange !== null) ? 20 : 0),
              fontSize: valueFontSize,
              fontWeight: 'bold',
              fill: textColor,
              textAlign: 'center',
            },
          },
          // Comparison labels
          ...(showComparison && (yoyChange !== null || momChange !== null) ? [
            {
              type: 'text',
              data: { value: null },
              style: {
                text: [
                  yoyChange !== null ? `同比 ${yoyChange >= 0 ? '↑' : '↓'} ${Math.abs(yoyChange).toFixed(1)}%` : '',
                  momChange !== null ? `环比 ${momChange >= 0 ? '↑' : '↓'} ${Math.abs(momChange).toFixed(1)}%` : '',
                ].filter(Boolean).join('  |  '),
                x: width / 2,
                y: height / 2 + valueFontSize / 2 + 8,
                fontSize: 13,
                fill: (yoyChange !== null && yoyChange >= 0) || (momChange !== null && momChange >= 0)
                  ? '#52c41a'
                  : (yoyChange !== null && yoyChange < 0) || (momChange !== null && momChange < 0)
                    ? '#ff4d4f'
                    : textColor,
                textAlign: 'center',
              },
            },
          ] : []),
        ],
      };
    }

    case 'big_number_trend': {
      const total = rows.reduce((sum, r) => sum + (Number(r[yCol]) || 0), 0);
      const lastVal = Number(rows[rows.length - 1]?.[yCol]) || 0;
      const prevVal = Number(rows[rows.length - 2]?.[yCol]) || lastVal;
      const changePercent = prevVal ? ((lastVal - prevVal) / prevVal * 100).toFixed(1) : '0';
      const isUp = lastVal >= prevVal;

      return {
        ...baseSpec,
        type: 'view',
        children: [
          {
            type: 'text',
            data: { value: null },
            style: {
              text: total.toLocaleString(),
              x: width / 2,
              y: height / 2 - 20,
              fontSize: 36,
              fontWeight: 'bold',
              fill: textColor,
              textAlign: 'center',
            },
          },
          {
            type: 'text',
            data: { value: null },
            style: {
              text: `${isUp ? '↑' : '↓'} ${changePercent}%`,
              x: width / 2,
              y: height / 2 + 20,
              fontSize: 14,
              fill: isUp ? '#52c41a' : '#ff4d4f',
              textAlign: 'center',
            },
          },
          {
            type: 'line',
            data: processedData,
            encode: { x: xCol, y: yCol },
            style: { stroke: colors[0], lineWidth: 2 },
            axis: false,
          },
        ],
      };
    }

    case 'timeseries_line':
    case 'timeseries_bar':
    case 'timeseries_area': {
      const timeCol = config?.timeCol || xCol;
      const valCol = config?.valCol || yCol;
      const tsGroupCol = detectGroupColumn(columns, rows, config);
      const tsHasGroups = tsGroupCol && ['timeseries_line', 'timeseries_bar', 'timeseries_area'].includes(chartType);

      let tsData = rows;
      if (isTimeColumn(rows.map(r => r[timeCol]))) {
        tsData = [...rows].sort((a, b) => {
          const da = parseTime(a[timeCol]);
          const db = parseTime(b[timeCol]);
          if (da && db) return da.getTime() - db.getTime();
          return 0;
        });
      }

      const isBar = chartType === 'timeseries_bar';
      const isArea = chartType === 'timeseries_area';

      const chartTypeG2 = isBar ? 'interval' : isArea ? 'area' : 'line';

      return {
        ...baseSpec,
        type: chartTypeG2,
        data: tsData,
        encode: { x: timeCol, y: valCol, color: tsHasGroups ? tsGroupCol! : undefined, shape: !isBar ? 'smooth' : undefined },
        axis: {
          x: { labelAutoRotate: true, style: axisLabelStyle },
          y: { style: axisLabelStyle },
        },
        scale: { color: { range: colors } },
        style: isBar ? { radiusTopLeft: 4, radiusTopRight: 4 } : { lineWidth: 2, fillOpacity: isArea ? 0.3 : 0 },
        interaction: { tooltip: { shared: true } },
      };
    }

    case 'tree':
    case 'treemap': {
      const nameCol = config?.nameCol || columns.find(c => typeof rows[0]?.[c] === 'string') || columns[0];
      const valCol = config?.valCol || columns.find(c => typeof rows[0]?.[c] === 'number') || columns[1];
      const parentCol = config?.parentCol || columns.find(c => typeof rows[0]?.[c] === 'string' && c !== nameCol);

      let treeData: any[];
      if (parentCol) {
        const nodeMap: Record<string, any> = {};
        rows.forEach(r => {
          const name = String(r[nameCol]);
          nodeMap[name] = { name, value: Number(r[valCol]) || 0, children: [] };
        });
        rows.forEach(r => {
          const parent = String(r[parentCol]);
          const name = String(r[nameCol]);
          if (nodeMap[parent] && parent !== name) {
            nodeMap[parent].children.push(nodeMap[name]);
          }
        });
        const roots = rows.filter(r => !nodeMap[String(r[parentCol])] || String(r[parentCol]) === String(r[nameCol]));
        treeData = roots.length > 0 ? [nodeMap[String(roots[0][nameCol])]] : [{ name: 'root', children: Object.values(nodeMap) }];
      } else {
        treeData = rows.map(r => ({ name: String(r[nameCol]), value: Number(r[valCol]) || 0 }));
      }

      return {
        ...baseSpec,
        type: chartType === 'tree' ? 'tree' : 'treemap',
        data: { value: { children: treeData } },
        encode: { value: 'value' },
        scale: { color: { range: colors } },
        style: { labelFill: textColor, labelFontSize: 12, labelFontWeight: '600' },
        legend: { color: { itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'waterfall': {
      const categoryCol = config?.categoryCol || xCol;
      const valCol = config?.valCol || yCol;
      let cumulative = 0;
      const waterfallData = rows.map((r, i) => {
        const val = Number(r[valCol]) || 0;
        if (i === 0) {
          cumulative = val;
          return { [categoryCol]: String(r[categoryCol]), start: 0, end: val, value: val };
        }
        const start = cumulative;
        cumulative += val;
        return { [categoryCol]: String(r[categoryCol]), start, end: cumulative, value: val };
      });

      return {
        ...baseSpec,
        type: 'interval',
        data: waterfallData,
        encode: { x: categoryCol, y: ['start', 'end'], color: (d: any) => d.value >= 0 ? 'increase' : 'decrease' },
        scale: { color: { domain: ['increase', 'decrease'], range: ['#52c41a', '#ff4d4f'] } },
        style: { radiusTopLeft: 4, radiusTopRight: 4 },
        axis: {
          x: { style: axisLabelStyle },
          y: { style: axisLabelStyle },
        },
        legend: { color: { itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'sankey': {
      const sourceCol = config?.sourceCol || columns.find(c => typeof rows[0]?.[c] === 'string') || columns[0];
      const targetCol = config?.targetCol || columns.find(c => typeof rows[0]?.[c] === 'string' && c !== sourceCol) || columns[1];
      const valueCol = config?.valueCol || columns.find(c => typeof rows[0]?.[c] === 'number') || columns[2];

      const nodeSet: Record<string, boolean> = {};
      const links = rows.map(r => {
        const source = String(r[sourceCol]);
        const target = String(r[targetCol]);
        nodeSet[source] = true;
        nodeSet[target] = true;
        return { source, target, value: Number(r[valueCol]) || 0 };
      });
      const nodes = Object.keys(nodeSet).map(name => ({ name }));

      return {
        ...baseSpec,
        type: 'view',
        children: [{
          type: 'sankey',
          data: { nodes, links },
          encode: { x: 'x', y: 'y', value: 'value' },
          style: { labelFill: textColor, labelFontSize: 12, labelFontWeight: '600' },
          interaction: { tooltip: {} },
        }],
      };
    }

    case 'boxplot': {
      const numCols = columns.filter(c => typeof rows[0]?.[c] === 'number');
      const dataCol = config?.dataCol || numCols[0] || columns[0];
      const categoryCol = config?.categoryCol || columns.find(c => typeof rows[0]?.[c] === 'string') || '';

      const calculateBoxStats = (values: number[]) => {
        const sorted = [...values].sort((a, b) => a - b);
        const n = sorted.length;
        return {
          min: sorted[0],
          q1: sorted[Math.floor(n * 0.25)],
          median: sorted[Math.floor(n * 0.5)],
          q3: sorted[Math.floor(n * 0.75)],
          max: sorted[n - 1],
        };
      };

      let boxData: any[];
      if (categoryCol) {
        const groups: Record<string, number[]> = {};
        rows.forEach(r => {
          const cat = String(r[categoryCol]);
          if (!groups[cat]) groups[cat] = [];
          groups[cat].push(Number(r[dataCol]) || 0);
        });
        boxData = Object.entries(groups).map(([cat, values]) => ({
          category: cat,
          ...calculateBoxStats(values),
        }));
      } else {
        const values = rows.map(r => Number(r[dataCol]) || 0);
        boxData = [{ category: dataCol, ...calculateBoxStats(values) }];
      }

      return {
        ...baseSpec,
        type: 'box',
        data: boxData,
        encode: { x: 'category', y: ['min', 'q1', 'median', 'q3', 'max'] },
        style: { stroke: colors[0], fill: colors[0], fillOpacity: 0.3 },
        axis: {
          x: { style: axisLabelStyle },
          y: { style: axisLabelStyle },
        },
        interaction: { tooltip: {} },
      };
    }

    case 'bubble': {
      const numCols = columns.filter(c => typeof rows[0]?.[c] === 'number');
      const bx = config?.xCol || numCols[0] || columns[0];
      const by = config?.yCol || numCols[1] || columns[1];
      const bSize = config?.sizeCol || numCols[2] || columns[2];
      const catCol = config?.categoryCol || columns.find(c => typeof rows[0]?.[c] === 'string');

      return {
        ...baseSpec,
        type: 'point',
        data: rows,
        encode: {
          x: bx,
          y: by,
          size: bSize,
          color: catCol || undefined,
        },
        scale: { color: { range: colors }, size: { range: [4, 20] } },
        style: { opacity: 0.7 },
        axis: {
          x: { style: axisLabelStyle },
          y: { style: axisLabelStyle },
        },
        legend: { color: { itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'calendar_heatmap': {
      const dateCol = config?.dateCol || columns.find(c => typeof rows[0]?.[c] === 'string') || columns[0];
      const valCol = config?.valCol || columns.find(c => typeof rows[0]?.[c] === 'number') || columns[1];

      return {
        ...baseSpec,
        type: 'cell',
        data: rows.map(r => ({ date: String(r[dateCol]), value: Number(r[valCol]) || 0 })),
        encode: { x: 'date', y: 'value', color: 'value' },
        scale: { color: { palette: isDark ? ['#14142a', '#177ddc', '#4992ff'] : ['#f0f5ff', '#5470c6', '#1a3a6e'] } },
        style: { inset: 1 },
        labels: [{ text: 'value', style: { fill: textColor, fontSize: 10 } }],
        axis: {
          x: { labelAutoRotate: true, style: axisLabelStyle },
          y: { style: axisLabelStyle },
        },
        legend: { color: { position: 'right', itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'rose': {
      const roseData = rows.map(r => ({
        name: String(r[xCol]),
        value: Number(r[yCol]) || 0,
      }));
      return {
        ...baseSpec,
        type: 'interval',
        data: roseData,
        encode: { x: 'name', y: 'value', color: 'name' },
        coordinate: { type: 'polar' },
        scale: { color: { range: colors } },
        style: { stroke: bgColor, lineWidth: 1 },
        labels: [{ text: 'name', style: { fill: textColor, fontSize: 11 } }],
        legend: { color: { position: 'right', itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'radial_bar': {
      return {
        ...baseSpec,
        type: 'interval',
        data: processedData,
        encode: { x: xCol, y: yCol, color: seriesField || undefined },
        coordinate: { type: 'polar', innerRadius: 0.5 },
        scale: { color: { range: colors } },
        style: { stroke: bgColor, lineWidth: 1 },
        axis: {
          x: { style: axisLabelStyle },
          y: { style: axisLabelStyle },
        },
        legend: { color: { itemLabelStyle: legendLabelStyle } },
        interaction: { tooltip: {} },
      };
    }

    case 'word_cloud': {
      const wordData = rows.map(r => ({
        text: String(r[xCol]),
        value: Number(r[yCol]) || 0,
      }));
      return {
        ...baseSpec,
        type: 'wordCloud',
        data: wordData,
        encode: { text: 'text', color: 'text' },
        scale: { color: { range: colors } },
        style: { fontSize: [12, 48] },
        layout: { spiral: 'rectangular' },
        interaction: { tooltip: {} },
      };
    }

    case 'china_map':
    case 'world_map': {
      // Map charts require special handling - return a placeholder spec
      // The actual rendering will be done asynchronously in the component
      const nameCol = config?.nameCol || columns.find(c => typeof rows[0]?.[c] === 'string') || columns[0];
      const valueCol = config?.valueCol || columns.find(c => typeof rows[0]?.[c] === 'number') || columns[1];

      // Prepare data as a map for quick lookup
      const dataMap: Record<string, number> = {};
      rows.forEach(r => {
        const name = String(r[nameCol]);
        const value = Number(r[valueCol]) || 0;
        dataMap[name] = value;
      });

      return {
        ...baseSpec,
        type: 'geoView',
        _dataMap: dataMap,
        _nameCol: nameCol,
        _valueCol: valueCol,
        _textColor: textColor,
        _bgColor: bgColor,
        _colors: colors,
      };
    }

    case 'table_value':
      // Rendered as HTML table in DashboardChartInner, not a G2 chart
      return { ...baseSpec, type: 'view', children: [] };

    default:
      // Default to bar chart
      return {
        ...baseSpec,
        type: 'interval',
        data: processedData,
        encode: { x: xCol, y: yCol, color: seriesField || undefined },
        axis: {
          x: { labelAutoRotate: true, style: axisLabelStyle },
          y: { style: axisLabelStyle },
        },
        scale: { color: { range: colors } },
        style: { radiusTopLeft: 4, radiusTopRight: 4 },
        interaction: { tooltip: { shared: true } },
      };
  }
}

function DashboardChartInner({ chartType, data, config, style, loading, chartId }: Props) {
  const isDark = useThemeStore(s => s.isDark);
  const isLoading = loading ?? (chartId != null && useDashboardStore.getState().refreshingChartIds.has(chartId));
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<Chart | null>(null);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const [mapData, setMapData] = useState<any>(null);

  const cfg = config || {};

  // Initialize date/date range default values before chart refresh
  useEffect(() => {
    if (!cfg.paramKey) return;
    const store = useDashboardStore.getState();
    const current = store.paramValues[cfg.paramKey];

    if (chartType === 'widget_daterange' && cfg.rangeMaxDays) {
      if (!current || (!current.start && !current.end)) {
        const today = new Date();
        const start = new Date(today);
        start.setDate(start.getDate() - (cfg.rangeMaxDays || 365));
        const fmt = (d: Date) => d.toISOString().slice(0, 10);
        store.setParamValue(cfg.paramKey, { start: fmt(start), end: fmt(today) });
      }
    } else if (chartType === 'widget_date' && cfg.defaultValue) {
      if (!current) {
        store.setParamValue(cfg.paramKey, cfg.defaultValue);
      }
    }
  }, [chartType, cfg.paramKey, cfg.rangeMaxDays, cfg.defaultValue]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-refresh for server-side pagination on mount (data_cache has full data, need paginated)
  useEffect(() => {
    if (chartId && chartType === 'table_value' && cfg.enableServerPagination && !loading) {
      const pageLimit = cfg.pageLimit || 20;
      useDashboardStore.getState().refreshSingleChart(chartId, { page_limit: pageLimit, page_offset: 0, count_sql: cfg.countSql });
    }
  }, [chartId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Serialize data/config to detect actual content changes (not just reference changes)
  const dataKey = data?.columns?.length
    ? data.columns.join(',') + '|' + data.rows.length + '|' + (data.rows[0] ? JSON.stringify(data.rows[0]) : '')
    : '';
  const configKey = cfg ? JSON.stringify(cfg) : '';

  // Measure container dimensions
  useEffect(() => {
    const measure = () => {
      if (containerRef.current) {
        const { clientWidth, clientHeight } = containerRef.current;
        if (clientWidth > 0 && clientHeight > 0) {
          setDimensions({ width: clientWidth, height: clientHeight });
        }
      }
    };

    measure();
    const timer = setTimeout(measure, 50);

    let observer: ResizeObserver | null = null;
    if (containerRef.current && typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => measure());
      observer.observe(containerRef.current);
    }

    window.addEventListener('resize', measure);

    return () => {
      clearTimeout(timer);
      window.removeEventListener('resize', measure);
      if (observer) observer.disconnect();
    };
  }, []);

  // Fetch map data for map charts
  useEffect(() => {
    if (chartType !== 'china_map' && chartType !== 'world_map') {
      setMapData(null);
      return;
    }

    const mapType = chartType === 'china_map' ? 'china' : 'world';
    const url = MAP_URLS[mapType];

    fetch(url)
      .then(res => res.json())
      .then(topoData => {
        const features = topojson.feature(topoData, topoData.objects.default || Object.values(topoData.objects)[0]);
        setMapData(features);
      })
      .catch(err => {
        console.error('Failed to load map data:', err);
      });
  }, [chartType]);

  // Build and render chart
  useEffect(() => {
    if (!containerRef.current || dimensions.width === 0 || dimensions.height === 0) return;
    if (!data.columns?.length || !data.rows?.length) return;

    // For map charts, wait for map data
    if ((chartType === 'china_map' || chartType === 'world_map') && !mapData) return;

    // Destroy previous chart
    if (chartRef.current) {
      chartRef.current.destroy();
      chartRef.current = null;
    }

    try {
      const spec = buildG2Spec(
        chartType, data.columns, data.rows, cfg, isDark,
        dimensions.width, dimensions.height
      );

      // Handle map charts
      if (chartType === 'china_map' || chartType === 'world_map') {
        const nameCol = cfg?.nameCol || data.columns.find(c => typeof data.rows[0]?.[c] === 'string') || data.columns[0];
        const valueCol = cfg?.valueCol || data.columns.find(c => typeof data.rows[0]?.[c] === 'number') || data.columns[1];

        // Prepare data as a map for quick lookup
        const dataMap: Record<string, number> = {};
        data.rows.forEach(r => {
          const name = String(r[nameCol]);
          const value = Number(r[valueCol]) || 0;
          dataMap[name] = value;
        });

        // Merge data into map features
        const featuresWithValues = mapData.features.map((f: any) => ({
          ...f,
          properties: {
            ...f.properties,
            value: dataMap[f.properties.name] || 0,
          },
        }));

        const chart = new Chart({
          container: containerRef.current,
          width: dimensions.width,
          height: dimensions.height,
          autoFit: false,
        });

        chart.options({
          type: 'geoView',
          children: [
            {
              type: 'geoPath',
              data: { type: 'FeatureCollection', features: featuresWithValues },
              encode: {
                color: 'value',
              },
              scale: {
                color: {
                  palette: 'ylGn',
                  unknown: '#f0f0f0',
                },
              },
              style: {
                stroke: '#fff',
                lineWidth: 0.5,
              },
              tooltip: {
                title: 'name',
                items: [{ field: 'value', name: '数值' }],
              },
            },
          ],
        });

        chart.render();
        chartRef.current = chart;
      } else {
        const chart = new Chart({
          container: containerRef.current,
          width: dimensions.width,
          height: dimensions.height,
          autoFit: false,
        });

        chart.options(spec);
        chart.render();
        chartRef.current = chart;
      }
    } catch (e) {
      console.error('Chart render error:', e);
    }

    return () => {
      if (chartRef.current) {
        chartRef.current.destroy();
        chartRef.current = null;
      }
    };
  }, [chartType, dataKey, configKey, dimensions, mapData]); // eslint-disable-line react-hooks/exhaustive-deps

  // Widget rendering — compact form controls, not G2 charts (buttons handled separately below)
  const WIDGET_TYPES = ['widget_label', 'widget_text', 'widget_number', 'widget_date', 'widget_daterange', 'widget_select', 'widget_multi_select'];
  if (WIDGET_TYPES.includes(chartType)) {
    const paramKey = cfg?.paramKey || '';
    const label = cfg?.label || paramKey;
    const placeholder = cfg?.placeholder || '';
    const defaultValue = cfg?.defaultValue ?? '';
    const labelPosition = cfg?.labelPosition || 'left';
    const showLabel = cfg?.showLabel !== false;
    const options: { label: string; value: string }[] = cfg?.options || [];
    const ws = cfg?.widgetStyle || {};

    const paramValues = useDashboardStore.getState().paramValues;
    const setParamValue = useDashboardStore.getState().setParamValue;
    const currentValue = paramValues[paramKey] ?? defaultValue;

    const handleChange = (val: any) => {
      if (paramKey) setParamValue(paramKey, val);
      // Bind filter value to page param(s) if bind_param is configured
      const bindParam = cfg?.bind_param;
      if (bindParam && typeof bindParam === 'string' && bindParam.trim()) {
        const setPageParamValue = useDashboardStore.getState().setPageParamValue;
        if (chartType === 'widget_daterange' && val && typeof val === 'object') {
          // dateRange: bind_param is comma-separated, e.g. "date_start,date_end"
          const parts = bindParam.split(',').map((s: string) => s.trim()).filter(Boolean);
          if (parts.length >= 1) setPageParamValue(parts[0], val.start || '');
          if (parts.length >= 2) setPageParamValue(parts[1], val.end || '');
        } else {
          // select, multi_select, text, number: single param name
          setPageParamValue(bindParam.trim(), val ?? '');
        }
      }
    };

    const isHorizontal = labelPosition === 'left' && showLabel;
    const hasLabel = showLabel && label && chartType !== 'widget_label';

    // Merged style: container + input, applied directly to the element (no wrapper div)
    const directStyle: React.CSSProperties = {
      display: 'flex',
      flexDirection: isHorizontal ? 'row' : 'column',
      alignItems: isHorizontal ? 'center' : 'stretch',
      justifyContent: 'center',
      gap: hasLabel ? 6 : 4,
      position: 'absolute', inset: 0,
      padding: hasLabel ? '4px 8px' : '4px',
      fontSize: 'clamp(10px, 1.2vw, 16px)',
      color: ws.textColor || 'inherit',
      boxSizing: 'border-box',
      borderRadius: 6,
      border: '1px solid hsl(var(--input))',
      background: 'hsl(var(--background))',
      outline: 'none',
      cursor: 'text',
    };

    const labelEl = hasLabel ? (
      <Label style={{
        fontSize: 'inherit', fontWeight: 500, color: 'hsl(var(--muted-foreground))',
        whiteSpace: 'nowrap', flexShrink: 0,
      }}>
        {label}
      </Label>
    ) : null;

    // widget_label — static text, direct <span>
    if (chartType === 'widget_label') {
      return (
        <div style={{
          display: 'flex', alignItems: 'center',
          justifyContent: ws.horizontalAlign || 'center',
          position: 'absolute', inset: 0,
          overflow: 'hidden',
          padding: '4px 8px',
          fontSize: 'clamp(10px, 1.2vw, 16px)',
          fontWeight: ws.fontWeight || 500,
          color: ws.textColor || 'hsl(var(--foreground))',
          lineHeight: 1.4, wordBreak: 'break-word',
          boxSizing: 'border-box',
        }}>
          {cfg?.content || cfg?.label || label || '文本标签'}
        </div>
      );
    }

    // widget_daterange — two inputs + separator, needs minimal wrapper
    if (chartType === 'widget_daterange') {
      const rangeMaxDays = cfg?.rangeMaxDays || 365; // default 1 year
      const startDate = currentValue?.start || '';
      const endDate = currentValue?.end || '';

      // Calculate min/max constraints for end date
      const addDays = (dateStr: string, days: number): string => {
        const d = new Date(dateStr);
        d.setDate(d.getDate() + days);
        return d.toISOString().slice(0, 10);
      };
      const endMin = startDate || '';
      const endMax = startDate ? addDays(startDate, rangeMaxDays) : '';

      const dateInputStyle: React.CSSProperties = {
        flex: 1, height: '100%', fontSize: 'inherit', borderRadius: 6,
        border: `1px solid ${isDark ? 'rgba(255,255,255,0.15)' : 'hsl(var(--input))'}`,
        background: isDark ? 'rgba(255,255,255,0.08)' : 'hsl(var(--background))',
        padding: '4px 8px', outline: 'none',
        color: isDark ? '#eee' : 'inherit',
        boxSizing: 'border-box',
      };

      return (
        <div onClick={e => e.stopPropagation()} style={directStyle}>
          {labelEl}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1, minHeight: 0 }}>
            <input type="date" value={startDate}
              max={endDate || undefined}
              onChange={e => handleChange({ ...(currentValue || {}), start: e.target.value })}
              style={dateInputStyle} />
            <span style={{ fontSize: '0.85em', color: isDark ? '#ccc' : 'hsl(var(--muted-foreground))', flexShrink: 0 }}>~</span>
            <input type="date" value={endDate}
              min={endMin} max={endMax || undefined}
              onChange={e => handleChange({ ...(currentValue || {}), end: e.target.value })}
              style={dateInputStyle} />
          </div>
        </div>
      );
    }

    // Single-element widgets — no wrapper div, label is inline
    const commonProps = {
      onClick: (e: React.MouseEvent) => e.stopPropagation(),
      onMouseDown: (e: React.MouseEvent) => e.stopPropagation(),
    };

    if (chartType === 'widget_text') {
      if (!hasLabel) return <input type="text" {...commonProps} value={currentValue || ''} onChange={e => handleChange(e.target.value)} placeholder={placeholder} style={directStyle} />;
      return <div {...commonProps} style={directStyle}>{labelEl}<input type="text" value={currentValue || ''} onChange={e => handleChange(e.target.value)} placeholder={placeholder} style={{ flex: 1, minHeight: 0, fontSize: 'inherit', border: 'none', background: 'transparent', outline: 'none', color: 'inherit', width: '100%' }} /></div>;
    }

    if (chartType === 'widget_number') {
      if (!hasLabel) return <input type="number" {...commonProps} value={currentValue ?? ''} onChange={e => handleChange(e.target.value)} placeholder={placeholder} min={cfg?.min} max={cfg?.max} step={cfg?.step || 1} style={directStyle} />;
      return <div {...commonProps} style={directStyle}>{labelEl}<input type="number" value={currentValue ?? ''} onChange={e => handleChange(e.target.value)} placeholder={placeholder} min={cfg?.min} max={cfg?.max} step={cfg?.step || 1} style={{ flex: 1, minHeight: 0, fontSize: 'inherit', border: 'none', background: 'transparent', outline: 'none', color: 'inherit', width: '100%' }} /></div>;
    }

    if (chartType === 'widget_date') {
      const dateProps = { onClick: (e: React.MouseEvent) => e.stopPropagation() };
      const dateInnerStyle: React.CSSProperties = {
        flex: 1, minHeight: 0, fontSize: 'inherit', borderRadius: 6,
        border: `1px solid ${isDark ? 'rgba(255,255,255,0.15)' : 'hsl(var(--input))'}`,
        background: isDark ? 'rgba(255,255,255,0.08)' : 'hsl(var(--background))',
        padding: '4px 8px', outline: 'none',
        color: isDark ? '#eee' : 'inherit',
        boxSizing: 'border-box', width: '100%',
      };
      if (!hasLabel) return <input type="date" {...dateProps} value={currentValue || ''} onChange={e => handleChange(e.target.value)} style={directStyle} />;
      return <div {...dateProps} style={directStyle}>{labelEl}<input type="date" value={currentValue || ''} onChange={e => handleChange(e.target.value)} style={dateInnerStyle} /></div>;
    }

    if (chartType === 'widget_select') {
      if (!hasLabel) return <select {...commonProps} value={currentValue || ''} onChange={e => handleChange(e.target.value || undefined)} style={{ ...directStyle, cursor: 'pointer' }}>
        <option value="">{placeholder || '请选择'}</option>
        {options.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
      </select>;
      return <div {...commonProps} style={directStyle}>{labelEl}<select value={currentValue || ''} onChange={e => handleChange(e.target.value || undefined)} style={{ flex: 1, minHeight: 0, fontSize: 'inherit', border: 'none', background: 'transparent', outline: 'none', color: 'inherit', cursor: 'pointer', width: '100%' }}>
        <option value="">{placeholder || '请选择'}</option>
        {options.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
      </select></div>;
    }

    if (chartType === 'widget_multi_select') {
      if (!hasLabel) return <select multiple {...commonProps} value={Array.isArray(currentValue) ? currentValue : []} onChange={e => handleChange(Array.from(e.target.selectedOptions, o => o.value))} style={{ ...directStyle, cursor: 'pointer', minHeight: 60 }}>
        {options.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
      </select>;
      return <div {...commonProps} style={directStyle}>{labelEl}<select multiple value={Array.isArray(currentValue) ? currentValue : []} onChange={e => handleChange(Array.from(e.target.selectedOptions, o => o.value))} style={{ flex: 1, minHeight: 0, fontSize: 'inherit', border: 'none', background: 'transparent', outline: 'none', color: 'inherit', cursor: 'pointer', width: '100%' }}>
        {options.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
      </select></div>;
    }
  }

  // Button widgets — search, reset, export
  const BUTTON_WIDGET_TYPES = ['widget_search', 'widget_reset', 'widget_export'];
  if (BUTTON_WIDGET_TYPES.includes(chartType)) {
    const label = cfg?.label || (chartType === 'widget_search' ? '搜索' : chartType === 'widget_reset' ? '重置' : '导出');

    const handleClick = (e: React.MouseEvent) => {
      e.stopPropagation();
      e.preventDefault();
      if (chartType === 'widget_search') {
        useDashboardStore.getState().refreshCharts();
      } else if (chartType === 'widget_reset') {
        const paramValues = useDashboardStore.getState().paramValues;
        const keys = Object.keys(paramValues);
        keys.forEach(k => useDashboardStore.getState().setParamValue(k, ''));
      } else if (chartType === 'widget_export') {
        const dashboards = useDashboardStore.getState().dashboards;
        const currentId = useDashboardStore.getState().currentId;
        const dashboard = dashboards.find(d => d.id === currentId);
        const targetChartId = cfg?.targetChartId;
        const targetChart = dashboard?.charts.find(c => c.id === targetChartId)
          || dashboard?.charts.find(c => !c.chart_type.startsWith('widget_'));
        if (targetChart?.data_cache) {
          try {
            const { columns, rows } = JSON.parse(targetChart.data_cache);
            const csv = [columns.join(','), ...rows.map((r: any) => columns.map((c: string) => String(r[c] ?? '')).join(','))].join('\n');
            const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = `${targetChart.name || 'export'}.csv`; a.click();
            URL.revokeObjectURL(url);
          } catch { /* ignore */ }
        }
      }
    };

    const isPrimary = chartType === 'widget_search';
    const ws = cfg?.widgetStyle || {};
    const btnTextColor = ws.textColor || (isPrimary ? 'hsl(var(--primary-foreground))' : 'inherit');
    const btnBgColor = ws.backgroundColor || (isPrimary ? 'hsl(var(--primary))' : 'hsl(var(--background))');
    const btnBorderColor = ws.borderColor || 'hsl(var(--input))';

    return (
      <button
        type="button"
        onClick={handleClick}
        onMouseDown={e => { e.stopPropagation(); (e.currentTarget as HTMLElement).style.transform = 'scale(0.95)'; }}
        onMouseUp={e => { (e.currentTarget as HTMLElement).style.transform = 'scale(1)'; }}
        onMouseLeave={e => { (e.currentTarget as HTMLElement).style.transform = 'scale(1)'; }}
        style={{
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          position: 'absolute', inset: 0,
          fontSize: 'inherit',
          borderRadius: 6,
          border: isPrimary ? 'none' : `1px solid ${btnBorderColor}`,
          background: btnBgColor,
          color: btnTextColor,
          padding: '4px 8px',
          gap: 6,
          cursor: 'pointer',
          transition: 'transform 0.15s ease',
          boxSizing: 'border-box',
        }}
      >
        {chartType === 'widget_search' && <Search style={{ width: '1.2em', height: '1.2em' }} />}
        {chartType === 'widget_reset' && <RotateCcw style={{ width: '1.2em', height: '1.2em' }} />}
        {chartType === 'widget_export' && <Download style={{ width: '1.2em', height: '1.2em' }} />}
        {label}
      </button>
    );
  }

  if (!data.columns?.length || !data.rows?.length) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        position: 'absolute', inset: 0, color: '#999',
      }}>
        暂无数据
      </div>
    );
  }

  // table_value: render as HTML table with pagination
  if (chartType === 'table_value') {
    const maxRows = cfg?.maxRows || 100;
    const showIndex = cfg?.showIndex !== false;
    const striped = cfg?.striped !== false;
    const pageSize = cfg?.pageSize || 20;
    const enableServerPagination = cfg?.enableServerPagination === true;
    const pageLimit = cfg?.pageLimit || pageSize;
    const serverTotal = data.total || 0; // total from backend
    const enablePagination = cfg?.enablePagination !== false && (enableServerPagination || data.rows.length > pageSize);
    const allRows = data.rows.slice(0, maxRows);

    // Drill-through link config
    const links: Array<{
      column: string;
      target_page_id: number;
      open_mode: 'new_page' | 'modal' | 'same_page';
      param_mapping: Record<string, string>;
    }> = cfg?.links || [];

    const [modalState, setModalState] = useState<{ pageId: number; params: Record<string, any> } | null>(null);

    const handleCellClick = (col: string, row: any) => {
      const linkDef = links.find(l => l.column === col);
      if (!linkDef) return;

      // Build params from param_mapping: targetParamName -> sourceColumnValue
      const mappedParams: Record<string, any> = {};
      for (const [targetParam, sourceCol] of Object.entries(linkDef.param_mapping || {})) {
        mappedParams[targetParam] = row[sourceCol];
      }

      if (linkDef.open_mode === 'new_page') {
        const qs = new URLSearchParams(mappedParams).toString();
        window.open(`/page/${linkDef.target_page_id}${qs ? '?' + qs : ''}`, '_blank');
      } else if (linkDef.open_mode === 'modal') {
        setModalState({ pageId: linkDef.target_page_id, params: mappedParams });
      } else if (linkDef.open_mode === 'same_page') {
        const setPageParamValue = useDashboardStore.getState().setPageParamValue;
        for (const [targetParam, value] of Object.entries(mappedParams)) {
          setPageParamValue(targetParam, value);
        }
      }
    };

    const [page, setPage] = useState(1);
    const totalPages = enableServerPagination
      ? Math.max(1, Math.ceil(serverTotal / pageLimit))
      : (enablePagination ? Math.ceil(allRows.length / pageSize) : 1);
    const currentPage = Math.min(page, totalPages);
    const rows = enableServerPagination
      ? allRows
      : (enablePagination ? allRows.slice((currentPage - 1) * pageSize, currentPage * pageSize) : allRows);
    const startIndex = enableServerPagination ? (currentPage - 1) * pageLimit : (enablePagination ? (currentPage - 1) * pageSize : 0);

    // Generate page numbers to display
    const pageNumbers: (number | '...')[] = [];
    if (totalPages <= 7) {
      for (let i = 1; i <= totalPages; i++) pageNumbers.push(i);
    } else {
      pageNumbers.push(1);
      if (currentPage > 3) pageNumbers.push('...');
      for (let i = Math.max(2, currentPage - 1); i <= Math.min(totalPages - 1, currentPage + 1); i++) {
        pageNumbers.push(i);
      }
      if (currentPage < totalPages - 2) pageNumbers.push('...');
      pageNumbers.push(totalPages);
    }

    const btnStyle = (active = false, disabled = false): React.CSSProperties => ({
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      minWidth: 28, height: 26, fontSize: 11, borderRadius: 4,
      border: '1px solid hsl(var(--border))',
      background: active ? 'hsl(var(--primary))' : 'hsl(var(--background))',
      color: active ? 'hsl(var(--primary-foreground))' : disabled ? 'hsl(var(--muted-foreground) / 0.5)' : 'inherit',
      cursor: disabled ? 'default' : 'pointer',
      padding: '0 6px',
      opacity: disabled ? 0.5 : 1,
    });

    const showRefreshBtn = chartId != null;

    return (
      <div className="chart-cell" style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', position: 'relative', ...style }}>
        {isLoading && (
          <div style={{
            position: 'absolute', inset: 0, zIndex: 10,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: isDark ? 'rgba(0,0,0,0.4)' : 'rgba(255,255,255,0.6)',
            borderRadius: 'inherit', backdropFilter: 'blur(2px)',
          }}>
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" style={{ animation: 'spin 1s linear infinite' }}>
              <circle cx="12" cy="12" r="10" stroke={isDark ? 'rgba(255,255,255,0.2)' : 'rgba(0,0,0,0.1)'} strokeWidth="3" />
              <path d="M12 2a10 10 0 0 1 10 10" stroke="hsl(var(--primary))" strokeWidth="3" strokeLinecap="round" />
            </svg>
          </div>
        )}
        {showRefreshBtn && (
          <button
            className="chart-refresh-btn"
            onClick={(e) => {
              e.stopPropagation();
              useDashboardStore.getState().refreshSingleChart(chartId!);
            }}
            title="刷新此图表"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
              style={isLoading ? { animation: 'spin 1s linear infinite' } : undefined}>
              <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
              <path d="M3 3v5h5" />
              <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
              <path d="M16 16h5v5" />
            </svg>
          </button>
        )}
        <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
          <table style={{
            width: '100%', borderCollapse: 'collapse',
            fontSize: 12, lineHeight: 1.5,
          }}>
            <thead>
              <tr style={{ borderBottom: '2px solid hsl(var(--border))', position: 'sticky', top: 0, background: 'hsl(var(--card))', zIndex: 1 }}>
                {showIndex && <th style={{ padding: '6px 8px', textAlign: 'left', fontWeight: 500, color: 'hsl(var(--muted-foreground))', width: 40 }}>#</th>}
                {data.columns.map(col => (
                  <th key={col} style={{ padding: '6px 8px', textAlign: 'left', fontWeight: 500, color: 'hsl(var(--muted-foreground))', whiteSpace: 'nowrap' }}>{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i} style={{
                  borderBottom: '1px solid hsl(var(--border))',
                  background: striped && i % 2 === 1 ? 'hsl(var(--muted) / 0.3)' : 'transparent',
                }}>
                  {showIndex && <td style={{ padding: '4px 8px', color: 'hsl(var(--muted-foreground))', fontSize: 11 }}>{startIndex + i + 1}</td>}
                  {data.columns.map(col => {
                    const val = row[col];
                    const isNum = typeof val === 'number';
                    const linkDef = links.find(l => l.column === col);
                    const isLink = !!linkDef;
                    return (
                      <td key={col} style={{
                        padding: '4px 8px',
                        textAlign: isNum ? 'right' : 'left',
                        fontFamily: isNum ? 'var(--font-mono, monospace)' : undefined,
                        whiteSpace: 'nowrap',
                      }}>
                        {isLink ? (
                          <span
                            onClick={(e) => { e.stopPropagation(); handleCellClick(col, row); }}
                            style={{
                              color: 'hsl(210, 100%, 56%)',
                              cursor: 'pointer',
                              textDecoration: 'underline',
                              textUnderlineOffset: 2,
                            }}
                            onMouseOver={(e) => { (e.currentTarget as HTMLElement).style.color = 'hsl(210, 100%, 46%)'; }}
                            onMouseOut={(e) => { (e.currentTarget as HTMLElement).style.color = 'hsl(210, 100%, 56%)'; }}
                          >
                            {val != null ? String(val) : '-'}
                          </span>
                        ) : (
                          val != null ? String(val) : '-'
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination footer */}
        {enablePagination && (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '4px 8px', borderTop: '1px solid hsl(var(--border))',
            flexShrink: 0, gap: 4, fontSize: 11,
          }}>
            <span style={{ color: 'hsl(var(--muted-foreground))' }}>
              {enableServerPagination ? `共 ${serverTotal} 行 · 第 ${currentPage}/${totalPages} 页` : `共 ${allRows.length} 行`}
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <button
                style={btnStyle(false, currentPage <= 1)}
                onClick={() => {
                  if (currentPage <= 1) return;
                  const newPage = currentPage - 1;
                  setPage(newPage);
                  if (enableServerPagination && chartId) {
                    useDashboardStore.getState().refreshSingleChart(chartId, { page_limit: pageLimit, page_offset: (newPage - 1) * pageLimit, count_sql: cfg.countSql });
                  }
                }}
                disabled={currentPage <= 1}
              >‹</button>
              {pageNumbers.map((p, idx) =>
                p === '...'
                  ? <span key={`e${idx}`} style={{ padding: '0 4px', color: 'hsl(var(--muted-foreground))' }}>…</span>
                  : <button
                      key={p}
                      style={btnStyle(p === currentPage)}
                      onClick={() => {
                        setPage(p as number);
                        if (enableServerPagination && chartId) {
                          useDashboardStore.getState().refreshSingleChart(chartId, { page_limit: pageLimit, page_offset: ((p as number) - 1) * pageLimit, count_sql: cfg.countSql });
                        }
                      }}
                    >{p}</button>
              )}
              <button
                style={btnStyle(false, currentPage >= totalPages)}
                onClick={() => {
                  if (currentPage >= totalPages) return;
                  const newPage = currentPage + 1;
                  setPage(newPage);
                  if (enableServerPagination && chartId) {
                    useDashboardStore.getState().refreshSingleChart(chartId, { page_limit: pageLimit, page_offset: (newPage - 1) * pageLimit, count_sql: cfg.countSql });
                  }
                }}
                disabled={currentPage >= totalPages}
              >›</button>
            </div>
          </div>
        )}

        {/* Drill-through modal */}
        {modalState && (
          <ModalPage
            pageId={modalState.pageId}
            params={modalState.params}
            open={!!modalState}
            onClose={() => setModalState(null)}
          />
        )}
      </div>
    );
  }

  // Show loading for map charts
  if ((chartType === 'china_map' || chartType === 'world_map') && !mapData) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        position: 'absolute', inset: 0, color: '#999',
      }}>
        加载地图数据中...
      </div>
    );
  }

  const isWidget = chartType.startsWith('widget_');

  return (
    <div
      ref={containerRef}
      className="chart-cell"
      style={{
        position: 'absolute', inset: 0,
        overflow: 'hidden', ...style,
      }}
    >
      {chartId != null && !isWidget && (
        <button
          className="chart-refresh-btn"
          onClick={(e) => {
            e.stopPropagation();
            useDashboardStore.getState().refreshSingleChart(chartId);
          }}
          title="刷新此图表"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            style={isLoading ? { animation: 'spin 1s linear infinite' } : undefined}>
            <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
            <path d="M3 3v5h5" />
            <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
            <path d="M16 16h5v5" />
          </svg>
        </button>
      )}
      {isLoading && (
        <div style={{
          position: 'absolute', inset: 0, zIndex: 10,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: isDark ? 'rgba(0,0,0,0.4)' : 'rgba(255,255,255,0.6)',
          borderRadius: 'inherit',
          backdropFilter: 'blur(2px)',
        }}>
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" style={{ animation: 'spin 1s linear infinite' }}>
            <circle cx="12" cy="12" r="10" stroke={isDark ? 'rgba(255,255,255,0.2)' : 'rgba(0,0,0,0.1)'} strokeWidth="3" />
            <path d="M12 2a10 10 0 0 1 10 10" stroke="hsl(var(--primary))" strokeWidth="3" strokeLinecap="round" />
          </svg>
        </div>
      )}
    </div>
  );
}

export default memo(DashboardChartInner);

export interface ChartTypeItem {
  value: string;
  label: string;
  icon: string; // Lucide icon name
  category: 'basic' | 'stat' | 'timeseries' | 'advanced' | 'geo' | 'widget';
}

export const CHART_TYPES: ChartTypeItem[] = [
  // 基础图表
  { value: 'bar', label: '柱状图', icon: 'BarChart3', category: 'basic' },
  { value: 'line', label: '折线图', icon: 'TrendingUp', category: 'basic' },
  { value: 'pie', label: '饼图', icon: 'PieChart', category: 'basic' },
  { value: 'area', label: '面积图', icon: 'Activity', category: 'basic' },
  { value: 'scatter', label: '散点图', icon: 'Circle', category: 'basic' },
  { value: 'radar', label: '雷达图', icon: 'Webhook', category: 'basic' },
  { value: 'funnel', label: '漏斗图', icon: 'Filter', category: 'basic' },
  { value: 'waterfall', label: '瀑布图', icon: 'ArrowDownUp', category: 'basic' },
  // 数据展示
  { value: 'text_display', label: '文本展示', icon: 'Type', category: 'stat' },
  { value: 'table_value', label: '表值图', icon: 'Table', category: 'stat' },
  { value: 'big_number_trend', label: '趋势大数字', icon: 'Hash', category: 'stat' },
  { value: 'gauge', label: '仪表盘', icon: 'Gauge', category: 'stat' },
  // 时间序列
  { value: 'timeseries_line', label: '时序折线图', icon: 'LineChart', category: 'timeseries' },
  { value: 'timeseries_bar', label: '时序柱状图', icon: 'BarChart', category: 'timeseries' },
  { value: 'timeseries_area', label: '时序面积图', icon: 'AreaChart', category: 'timeseries' },
  { value: 'calendar_heatmap', label: '日历热力图', icon: 'Calendar', category: 'timeseries' },
  // 高级图表
  { value: 'heatmap', label: '热力图', icon: 'Grid3x3', category: 'advanced' },
  { value: 'boxplot', label: '箱线图', icon: 'Box', category: 'advanced' },
  { value: 'bubble', label: '气泡图', icon: 'Aperture', category: 'advanced' },
  { value: 'sankey', label: '桑基图', icon: 'ArrowRightLeft', category: 'advanced' },
  { value: 'tree', label: '树图', icon: 'GitBranch', category: 'advanced' },
  { value: 'treemap', label: '矩形树图', icon: 'LayoutGrid', category: 'advanced' },
  { value: 'rose', label: '玫瑰图', icon: 'Flower2', category: 'advanced' },
  { value: 'radial_bar', label: '径向柱状图', icon: 'Target', category: 'advanced' },
  { value: 'word_cloud', label: '词云图', icon: 'Cloud', category: 'advanced' },
  // 地理图表
  { value: 'china_map', label: '中国地图', icon: 'Map', category: 'geo' },
  { value: 'world_map', label: '世界地图', icon: 'Globe', category: 'geo' },
  // 参数控件
  { value: 'widget_label', label: '文本展示框', icon: 'Type', category: 'widget' },
  { value: 'widget_text', label: '文本输入框', icon: 'Type', category: 'widget' },
  { value: 'widget_number', label: '数字输入框', icon: 'Hash', category: 'widget' },
  { value: 'widget_date', label: '日期选择器', icon: 'Calendar', category: 'widget' },
  { value: 'widget_daterange', label: '日期范围', icon: 'Calendar', category: 'widget' },
  { value: 'widget_select', label: '下拉选择框', icon: 'List', category: 'widget' },
  { value: 'widget_multi_select', label: '多选下拉框', icon: 'ListChecks', category: 'widget' },
  { value: 'widget_search', label: '搜索按钮', icon: 'Search', category: 'widget' },
  { value: 'widget_reset', label: '重置按钮', icon: 'RotateCcw', category: 'widget' },
  { value: 'widget_export', label: '导出按钮', icon: 'Download', category: 'widget' },
];

export const CHART_TYPE_CATEGORIES = [
  { key: 'basic', label: '基础图表' },
  { key: 'stat', label: '数据展示' },
  { key: 'timeseries', label: '时间序列' },
  { key: 'advanced', label: '高级图表' },
  { key: 'geo', label: '地理图表' },
  { key: 'widget', label: '参数控件' },
] as const;
