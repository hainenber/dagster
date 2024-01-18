import * as React from 'react';
import {gql, useQuery} from '@apollo/client';
import {Link} from 'react-router-dom';

import {Tag} from '@dagster-io/ui-components';

import {RunStatusTagWithID} from '../../runs/RunStatusTag';
import {
  OldRunStatusOnlyQuery,
  OldRunStatusOnlyQueryVariables,
} from './types/AutomaterializeRunTag.types';

interface Props {
  runId: string;
}

export const AutomaterializeRunTag = ({runId}: Props) => {
  const {data, loading} = useQuery<OldRunStatusOnlyQuery, OldRunStatusOnlyQueryVariables>(
    RUN_STATUS_ONLY,
    {
      variables: {runId},
    },
  );

  if (loading && !data) {
    return <Tag icon="spinner">Loading</Tag>;
  }

  const run = data?.runOrError;
  if (run?.__typename !== 'Run') {
    return (
      <Tag icon="error" intent="danger">
        Run not found
      </Tag>
    );
  }

  return (
    <Link to={`/runs/${runId}`}>
      <RunStatusTagWithID runId={runId} status={run.status} />
    </Link>
  );
};

export const RUN_STATUS_ONLY = gql`
  query OldRunStatusOnlyQuery($runId: ID!) {
    runOrError(runId: $runId) {
      ... on Run {
        id
        status
      }
    }
  }
`;
