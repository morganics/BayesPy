# -*- coding: utf-8 -*-
"""
Created on Tue Apr 19 16:31:18 2016

@author: imorgan.admin
"""

from bayespy.jni import bayesServerInference
import bayespy.network
import pandas as pd
from bayespy.jni import bayesServer
from bayespy.jni import bayesServerParams
from bayespy.jni import jp
from bayespy.data import DataFrame

from collections import defaultdict

class QueryOutput:
    def __init__(self, continuous, discrete):
        self.continuous = continuous
        self.discrete = discrete

class Query:

    _factory = bayesServerInference.RelevanceTreeInferenceFactory()
    def __init__(self, jnetwork, inference):
        self._queryOptions = self._factory.createQueryOptions();
        self._queryOutput = self._factory.createQueryOutput();
        self._queryDistributions = inference.getQueryDistributions()
        self._inference = inference
        self._jnetwork = jnetwork

    def execute(self, variables=None):
        """
        Query a number of variables (if none, then query all variables in the network)
        :param variables: a list of variables, or none
        :return: a QueryOutput object with separate continuous/ discrete dataframes
        """
        distributions = {}
        if variables is None:
            variables = self._jnetwork.getVariables()

        for v in variables:
            if bayespy.network.is_variable_discrete(v):
                table = bayesServer.Table(v)
            else:
                table = bayesServer.CLGaussian(v)

            self._queryDistributions.add(bayesServerInference.QueryDistribution(table))
            distributions.update({ v.getName() : table})

        self._inference.query(self._queryOptions, self._queryOutput)

        states= []
        d_variables = []
        values = []
        c_variables = []
        mean = []
        variance = []
        for v in variables:
            dist =  distributions[v.getName()]
            if bayespy.network.is_variable_discrete(v):
                for state in v.getStates():
                    states.append(state.getName())
                    values.append(float(dist.get([state])))
                    d_variables.append(v.getName())
            else:
                variance.append(float(dist.getVariance(v)))
                mean.append(float(dist.getMean(v)))
                c_variables.append(v.getName())

        return QueryOutput(pd.DataFrame({ 'variable': c_variables, 'mean': mean, 'variance': variance}), pd.DataFrame({ 'variable' : d_variables, 'state': states, 'value' : values}))

class Evidence:

    def __init__(self, jnetwork, inference):
        self._jnetwork = jnetwork
        self._inference = inference
        self._evidence = inference.getEvidence()
        self._evidence.clear()
        self._variables = jnetwork.getVariables()

    def apply(self, evidence):
        """
        Apply evidence to a network
        :param evidence: if a discrete variable, then a list of strings generated by network.Discrete.tostring(),
        if continuous, then a list of tuples with (VariableName, ContinuousValue)
        :return: Nothing
        """
        for value in evidence:
            if not isinstance(value, tuple):
                node, state = value.split(bayespy.network.STATE_DELIMITER)
                v = self._variables.get(node)
                if bayespy.network.is_variable_discrete(v):
                    st = v.getStates().get(state)
                    if st is None:
                        raise ValueError("State {} does not exist in variable {}".format(state, node))

                    self._evidence.setState(st)
                else:
                    raise ValueError("Can not find state on a continuous variable")
            else:
                v = self._variables.get(value[0])
                if not bayespy.network.is_variable_continuous(v):
                    raise ValueError("Variable is not continuous")

                self._evidence.set(v, jp.java.lang.Double(value[1]))

class TemporalNetworkModel:

    def __init__(self, data, jnetwork, datastore):
        self._jnetwork = jnetwork
        self._table = datastore.uuid
        self._factory = bayesServerInference.RelevanceTreeInferenceFactory()

        self._inference = self._factory.createInferenceEngine(jnetwork)
        self._queryOptions = self._factory.createQueryOptions()
        self._queryOutput = self._factory.createQueryOutput()

        self._datastore = datastore

        self._data = data

class NetworkModel:

    def __init__(self, data, jnetwork, datastore):
        self._jnetwork = jnetwork
        self._factory = bayesServerInference.RelevanceTreeInferenceFactory()

        self._inference = self._factory.createInferenceEngine(jnetwork)
        self._queryOptions = self._factory.createQueryOptions()
        self._queryOutput = self._factory.createQueryOutput()

        self._datastore = datastore

        self._data = data

    def get_network(self):
        return self._jnetwork

    def _get_datareadercommand(self, indexes):
        """
        Get the data reader
        :param indexes: training/ testing indexes
        :return: a a DatabaseDataReaderCommand
        """
        dataReaderCommand = bayesServer.data.DatabaseDataReaderCommand(
                self._datastore.get_connection(),
                "select * from {} where ix in ({})".format(self._datastore.table, ",".join(str(i) for i in indexes)))

        return dataReaderCommand

    def _create_variablereferences(self, data):
        """
        Match up network variables to the dataframe columns
        :param data: dataframe
        :return: a list of 'VariableReference' objects
        """
        latent_variable_name = "Cluster"
        for v in self._jnetwork.getVariables():
            if v.getName().startswith(latent_variable_name):
                continue

            valueType = bayesServer.data.ColumnValueType.VALUE

            if bayespy.network.is_variable_discrete(v):
                if not DataFrame.is_int(data[v.getName()].dtype) and not DataFrame.is_bool(data[v.getName()].dtype):
                    valueType = bayesServer.data.ColumnValueType.NAME

            yield bayesServer.data.VariableReference(v, valueType, v.getName())

    def inference(self):
        return self._factory.createInferenceEngine(self._jnetwork);

    def create_query(self, inference):
        return Query(self._jnetwork, inference)

    def evidence(self, inference):
        return Evidence(self._jnetwork, inference)

    def save(self, path):
        from xml.dom import minidom
        nt = self._jnetwork.saveToString()
        reparsed = minidom.parseString(nt)
        with open(path, 'w') as fh:
            fh.write(reparsed.toprettyxml(indent="  "))


    def train(self, indexes):
        """
        Train a model on data provided in the constructor
        :param indexes: the 'training' indexes, if using KFold cross validation etc.
        :return:
        """
        learning = bayesServerParams.ParameterLearning(self._jnetwork, bayesServerInference.RelevanceTreeInferenceFactory())
        learningOptions = bayesServerParams.ParameterLearningOptions()
        #learningOptions.setCalculateStatistics(True)

        dataReaderCommand = self._get_datareadercommand(indexes)

        readerOptions = bayesServer.data.ReaderOptions()

        variableRefs = list(self._create_variablereferences(self._data))

        evidenceReaderCommand = bayesServer.data.DefaultEvidenceReaderCommand(dataReaderCommand, jp.java.util.Arrays.asList(variableRefs), readerOptions)

        result = learning.learn(evidenceReaderCommand, learningOptions)

        return {'Converged': result.getConverged(), 'Loglikelihood': result.getLogLikelihood().floatValue(),
                    'IterationCount': result.getIterationCount(), 'CaseCount': result.getCaseCount(),
                    'WeightedCaseCount': result.getWeightedCaseCount(), 'UnweightedCaseCount':  result.getUnweightedCaseCount(),
                    'BIC': result.getBIC().floatValue()}

    def predict(self, indexes, targets=[]):
        """
        Predict the output class given the trained model.
        :param indexes: Testing indexes (if using cross validation etc)
        :param targets: A list of target variables that are being queried
        :return: The list of target variables with probabilities/ states given the testing data
        """
        self._queryOptions.setQueryEvidenceMode(bayesServerInference.QueryEvidenceMode.RETRACT_QUERY_EVIDENCE)
        dataReaderCommand = self._get_datareadercommand(indexes)

        target_nodes = []
        for target_node in targets:
            v = self._jnetwork.getVariables().get(target_node)
            if bayespy.network.is_variable_continuous(v):
                n = bayesServer.CLGaussian(v)
            else:
                n = bayesServer.Table(v)


            self._inference.getQueryDistributions().add(bayesServerInference.QueryDistribution(n));
            target_nodes.append((v, n))

        dataReader = dataReaderCommand.executeReader()
        readerOptions = bayesServer.data.ReaderOptions()
        variableRefs = list(self._create_variablereferences(self._data))
        reader = bayesServer.data.DefaultEvidenceReader(dataReader, jp.java.util.Arrays.asList(variableRefs), readerOptions)

        results = defaultdict(list)
        while reader.read(self._inference.getEvidence(), bayesServer.data.DefaultReadOptions(True)):
            try:
                self._inference.query(self._queryOptions, self._queryOutput)
            except BaseException as e:
                print(e)

            for v, target in target_nodes:
                if bayespy.network.is_variable_continuous(v):
                    #continuous
                    results[v.getName()].append(target.getMean(v))
                else:
                    # discrete
                    states = {}

                    for state in v.getStates():
                        states.update({ state.getName() : target.get([state])})

                    max_state = max(states.keys(), key=(lambda key: states[key]))
                    if DataFrame.is_int(self._data[v.getName()].dtype):
                        max_state = int(max_state)

                    states.update({'MaxStateLikelihood': max_state})

                    results[v.getName()].append(states)


            self._inference.getEvidence().clear()

        reader.close()
        dataReader.close()
        return {k:pd.DataFrame(v) for k,v in results.items()}

    def fit(self, data):
        return self.train(data)

    def transform(self, data, targets=[]):
        return self.predict(self, data, targets)





