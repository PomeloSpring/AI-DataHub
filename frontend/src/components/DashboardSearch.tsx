import { useState, useCallback, useMemo } from 'react';
import { Search, Star, Clock, BarChart3 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { ScrollArea } from '@/components/ui/scroll-area';

interface DashboardSearchProps {
  dashboards: any[];
  onSelect: (dashboardId: number) => void;
  onToggleFavorite: (dashboardId: number) => void;
  favorites: number[];
}

export default function DashboardSearch({
  dashboards, onSelect, onToggleFavorite, favorites,
}: DashboardSearchProps) {
  const [searchText, setSearchText] = useState('');
  const [filterType, setFilterType] = useState<'all' | 'favorites' | 'recent'>('all');

  const filteredDashboards = useMemo(() => {
    let filtered = dashboards;

    if (searchText) {
      const searchLower = searchText.toLowerCase();
      filtered = filtered.filter(d =>
        d.name.toLowerCase().includes(searchLower) ||
        (d.description && d.description.toLowerCase().includes(searchLower))
      );
    }

    switch (filterType) {
      case 'favorites':
        filtered = filtered.filter(d => favorites.includes(d.id));
        break;
      case 'recent':
        filtered = [...filtered].sort((a, b) =>
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
        ).slice(0, 10);
        break;
    }

    return filtered;
  }, [dashboards, searchText, filterType, favorites]);

  const handleToggleFavorite = useCallback((e: React.MouseEvent, dashboardId: number) => {
    e.stopPropagation();
    onToggleFavorite(dashboardId);
  }, [onToggleFavorite]);

  const formatDate = useCallback((dateStr: string) => {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    return date.toLocaleDateString('zh-CN', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }, []);

  return (
    <div className="flex flex-col h-full">
      {/* Search Header */}
      <div className="p-4 border-b space-y-3">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="搜索仪表盘..."
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            className="pl-9"
          />
        </div>

        <div className="flex gap-2">
          <Button
            variant={filterType === 'all' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setFilterType('all')}
          >
            全部
          </Button>
          <Button
            variant={filterType === 'favorites' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setFilterType('favorites')}
          >
            <Star className="h-4 w-4 mr-1" />
            收藏
          </Button>
          <Button
            variant={filterType === 'recent' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setFilterType('recent')}
          >
            <Clock className="h-4 w-4 mr-1" />
            最近
          </Button>
        </div>
      </div>

      {/* Results */}
      <ScrollArea className="flex-1 p-4">
        {filteredDashboards.length === 0 ? (
          <div className="text-center py-12 text-muted-foreground">
            {searchText ? '未找到匹配的仪表盘' : '暂无仪表盘'}
          </div>
        ) : (
          <div className="space-y-2">
            {filteredDashboards.map((dashboard) => (
              <div
                key={dashboard.id}
                className="flex items-center gap-3 p-3 rounded-lg border cursor-pointer hover:bg-muted transition-colors"
                onClick={() => onSelect(dashboard.id)}
              >
                <Avatar className="h-8 w-8">
                  <AvatarFallback className={favorites.includes(dashboard.id) ? 'bg-yellow-500' : 'bg-primary'}>
                    <BarChart3 className="h-4 w-4 text-white" />
                  </AvatarFallback>
                </Avatar>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium truncate">{dashboard.name}</span>
                    {dashboard.is_default && (
                      <Badge variant="secondary" className="text-xs">默认</Badge>
                    )}
                  </div>
                  {dashboard.description && (
                    <p className="text-xs text-muted-foreground truncate mt-0.5">
                      {dashboard.description}
                    </p>
                  )}
                  <div className="flex items-center gap-3 mt-1">
                    <span className="text-xs text-muted-foreground">
                      {dashboard.charts?.length || 0} 个图表
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {formatDate(dashboard.updated_at)}
                    </span>
                  </div>
                </div>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 p-0"
                      onClick={(e) => handleToggleFavorite(e, dashboard.id)}
                    >
                      <Star className={`h-4 w-4 ${favorites.includes(dashboard.id) ? 'fill-yellow-500 text-yellow-500' : 'text-muted-foreground'}`} />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>{favorites.includes(dashboard.id) ? '取消收藏' : '收藏'}</TooltipContent>
                </Tooltip>
              </div>
            ))}
          </div>
        )}
      </ScrollArea>
    </div>
  );
}

// Hook for managing favorites
export function useDashboardFavorites() {
  const [favorites, setFavorites] = useState<number[]>(() => {
    try {
      const saved = localStorage.getItem('dashboard_favorites');
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });

  const toggleFavorite = useCallback((dashboardId: number) => {
    setFavorites(prev => {
      const newFavorites = prev.includes(dashboardId)
        ? prev.filter(id => id !== dashboardId)
        : [...prev, dashboardId];
      localStorage.setItem('dashboard_favorites', JSON.stringify(newFavorites));
      return newFavorites;
    });
  }, []);

  const isFavorite = useCallback((dashboardId: number) => {
    return favorites.includes(dashboardId);
  }, [favorites]);

  return {
    favorites,
    toggleFavorite,
    isFavorite,
  };
}
